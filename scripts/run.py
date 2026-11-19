#!/usr/bin/env python3
"""After a change, ask Glean which definitions call the changed ones, and list
the files whose saved descriptions therefore need rereading.

Steps, for a parent directory and a changed directory of Python files:
  1. Pyrefly writes Glean facts for the parent; each source file is one unit.
  2. Glean builds a parent database from those facts.
  3. Glean builds an incremental database on top. Two lists of files are kept
     apart:
       - edited: the files whose bytes changed;
       - re-indexed: the files whose facts are written again.
     In mode "edited_only" the two lists are the same. In mode "importers" the
     re-indexed list also has every unedited file that imports an edited file,
     directly or through other files, in either version, because its call and
     reference facts can point at a definition that moved. Mode
     "direct_importers" stops after one import, to show when that is not
     enough. Every re-indexed file's unit is excluded from the parent.
  4. Definitions in the edited files are compared, old text against new text.
  5. For each changed or removed definition, both the parent and the new
     database are asked for its callers (python.CalleeToCaller).
  6. Callers in files that were not edited are the descriptions to reread.
     That uses the edited list, not the re-indexed one: a file can need
     indexing again without its description needing rereading, and the
     other way round.
  7. The new database is compared with a fresh full index of the same files,
     for every predicate either one holds.

The glean binary runs in a container (GLEAN_CONTAINER); this script runs on the
host and calls it with `docker exec`.
"""
import ast, json, os, pathlib, re, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTAINER = os.environ.get("GLEAN_CONTAINER", "glean")
GLEAN = ["docker", "exec", CONTAINER, "glean", "--db-root", "/tmp/db",
         "--schema", "dir:/glean-demo/schema/source"]
DROPPED = set()   # predicates the Glean schema in use does not define
LOG = []


def glean(*args, check=True):
    p = subprocess.run(GLEAN + list(args), capture_output=True, text=True)
    LOG.append("$ glean " + " ".join(args) + "\n" + p.stdout[-2000:] + p.stderr[-4000:])
    if check and p.returncode != 0:
        print(LOG[-1])
        sys.exit(f"glean {args[0]} failed")
    return p


def py_files(srcdir):
    return {str(q.relative_to(srcdir)) for q in pathlib.Path(srcdir).rglob("*.py")}


def pyrefly_facts(srcdir, outdir, only=None):
    """Write one JSON fact file per source file, owned by a unit named after it."""
    outdir.mkdir(parents=True, exist_ok=True)
    counts = {}
    # With `only`, Pyrefly is given just those files to check. It still reads
    # the other files when it resolves their imports.
    targets = ["./**/*.py"] if only is None else sorted("./" + f for f in only)
    with tempfile.TemporaryDirectory() as tmp:
        p = subprocess.run(
            ["pyrefly", "check", "--check-all", f"--report-glean={tmp}/output",
             "--output-format=omit-errors", "--summary=none", *targets,
             "--search-path=."], cwd=srcdir, capture_output=True, text=True)
        if p.returncode != 0:
            print(p.stdout[-4000:], p.stderr[-4000:])
            sys.exit(f"pyrefly failed (exit {p.returncode}) in {srcdir}")
        for f in sorted(pathlib.Path(tmp, "output").glob("*.json")):
            batches = json.loads(f.read_text())
            src = next(b["facts"][0]["key"]["file"]["key"] for b in batches
                       if b.get("predicate", "").startswith("src.FileLanguage"))
            if only is not None and src not in only:
                continue
            kept = [dict(b, unit=src) for b in batches
                    if b.get("predicate") and b["facts"] and b["predicate"] not in DROPPED]
            (outdir / (src.replace("/", "__") + ".json")).write_text(json.dumps(kept))
            counts[src] = sum(len(b["facts"]) for b in kept)
    # A run that wrote facts for only some of the files must not pass as an
    # update: the missing files would silently keep their parent's facts.
    wanted = set(only) if only is not None else py_files(srcdir)
    if set(counts) != wanted:
        sys.exit(f"pyrefly wrote facts for {sorted(counts)}, expected {sorted(wanted)}")
    return counts


def write_db(db, srcdir, factdir, only=None):
    """Write facts, dropping any predicate this Glean schema does not define."""
    while True:
        for old in factdir.glob("*.json"):
            old.unlink()
        counts = pyrefly_facts(srcdir, factdir, only)
        rel = factdir.relative_to(ROOT)
        files = [f"/work/{rel}/{f.name}" for f in sorted(factdir.glob("*.json"))]
        p = glean("write", "--db", db, *files, check=False)
        m = re.search(r"not in scope: ([\w.]+)", p.stdout + p.stderr)
        if m and m.group(1) not in DROPPED:
            DROPPED.add(m.group(1))
            continue
        if p.returncode != 0 or "DB write failure" in p.stdout + p.stderr:
            print(LOG[-1])
            sys.exit("glean write failed")
        return counts


def query(db, angle):
    p = glean("query", "--db", db, "--recursive", angle)
    out = p.stdout.strip()
    if not out:
        return []
    try:
        v = json.loads(out)
        return v if isinstance(v, list) else [v]
    except json.JSONDecodeError:
        return [json.loads(l) for l in out.splitlines() if l.strip().startswith("{")]


def definitions(db, srcdir, files=None):
    """name -> {kind, file, start, length, text} for classes, functions, variables."""
    if files is None:
        rows = query(db, "python.DefinitionLocation _")
    else:
        rows = []
        for f in files:
            rows += query(db, 'python.DefinitionLocation { file = "%s" }' % f)
    defs = {}
    for r in rows:
        k = r["key"]
        (kind, d), = k["definition"].items()
        if kind == "module":
            continue
        name = d["key"]["declaration"]["key"]["name"]["key"]
        if ".<locals>." in name:
            continue
        defs[name] = dict(kind=kind, file=k["file"]["key"],
                          start=k["span"]["start"], length=k["span"]["length"])
    for name, d in defs.items():
        # A definition's own text: its span with nested definitions blanked out,
        # so a class is not marked changed only because one of its methods was.
        data = bytearray((pathlib.Path(srcdir) / d["file"]).read_bytes())
        end = d["start"] + d["length"]
        for other, o in defs.items():
            inside = d["start"] <= o["start"] and o["start"] + o["length"] <= end
            same = (o["start"], o["length"]) == (d["start"], d["length"])
            if other != name and o["file"] == d["file"] and inside and not same:
                data[o["start"]:o["start"] + o["length"]] = b" " * o["length"]
        d["text"] = bytes(data[d["start"]:end]).decode()
    return defs


# ---------------------------------------------------------------- imports

def local_imports(srcdir):
    """file -> set of files in srcdir that it imports, read with Python's ast.
    This stands in for a build system's dependency list on this fixture."""
    srcdir = pathlib.Path(srcdir)
    files = py_files(srcdir)

    def module_file(mod):
        base = mod.replace(".", "/")
        for cand in (base + ".py", base + "/__init__.py"):
            if cand in files:
                return cand
        return None

    graph = {}
    for f in sorted(files):
        pkg = f.rsplit("/", 1)[0].replace("/", ".") if "/" in f else ""
        deps = set()
        for node in ast.walk(ast.parse((srcdir / f).read_text(), f)):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    parts = pkg.split(".") if pkg else []
                    parts = parts[:len(parts) - (node.level - 1)] if node.level > 1 else parts
                    head = ".".join(parts + ([node.module] if node.module else []))
                else:
                    head = node.module or ""
                # `from pkg import sub` can name a module as well as a value.
                mods = [head] + [f"{head}.{a.name}" if head else a.name for a in node.names]
            else:
                continue
            for m in mods:
                hit = module_file(m) if m else None
                if hit and hit != f:
                    deps.add(hit)
        graph[f] = deps
    return graph


def importers_of(edited, old_dir, new_dir, transitive=True):
    """Unedited files that import an edited file, in the old or the new
    version, directly or (if transitive) through other files."""
    reverse = {}
    for d in (old_dir, new_dir):
        for f, deps in local_imports(d).items():
            for dep in deps:
                reverse.setdefault(dep, set()).add(f)
    seen, todo = set(), list(edited)
    while todo:
        f = todo.pop()
        for imp in reverse.get(f, ()):
            if imp not in seen and imp not in edited:
                seen.add(imp)
                if transitive:
                    todo.append(imp)
    return sorted(seen)


# ---------------------------------------------------------------- comparison

KEY_PREDICATES = ["python.CalleeToCaller", "python.DefinitionLocation",
                  "python.DeclarationLocation", "python.FileCall",
                  "python.XRefsViaNameByFile", "src.File"]


def strip_ids(v):
    if isinstance(v, dict):
        return {k: strip_ids(x) for k, x in v.items() if k != "id"}
    if isinstance(v, list):
        return [strip_ids(x) for x in v]
    return v


def fact_set(db, predicate):
    out = set()
    for r in query(db, predicate + " _"):
        out.add(json.dumps(strip_ids({k: r[k] for k in ("key", "value") if k in r}),
                           sort_keys=True))
    return out


def call_pairs(facts):
    out = []
    for f in sorted(facts):
        k = json.loads(f)["key"]
        out.append(f'{k["caller"]["key"]} calls {k["callee"]["key"]}')
    return out


def predicates_in(*factdirs):
    names = set()
    for d in factdirs:
        for f in d.glob("*.json"):
            for b in json.loads(f.read_text()):
                names.add(re.sub(r"\.\d+$", "", b["predicate"]))
    return sorted(names | {"src.File"})


FULL = {}


def full_index(name, new_dir):
    if name not in FULL:
        full = f"ex/{name}_full"
        glean("create", "--db", full)
        write_db(full, new_dir, ROOT / "facts" / (name + "_full"))
        glean("complete", "--db", full)
        glean("finish", "--db", full)
        FULL[name] = full
    return FULL[name]


def compare_with_full_index(name, db, new_dir, factdirs):
    """Index the changed directory from scratch and compare what the two
    databases hold, by content (names, relations, locations), not fact ids,
    for every predicate either one was given."""
    full = full_index(name, new_dir)
    preds = predicates_in(ROOT / "facts" / (name + "_full"), *factdirs)
    result, differing = {}, []
    for pred in preds:
        inc, ful = fact_set(db, pred), fact_set(full, pred)
        row = dict(incremental=len(inc), full=len(ful), same=inc == ful)
        if inc != ful:
            differing.append(pred)
            if pred == "python.CalleeToCaller":
                row["only_in_incremental"] = call_pairs(inc - ful)
                row["only_in_full"] = call_pairs(ful - inc)
            else:
                row["only_in_incremental"] = sorted(inc - ful)[:20]
                row["only_in_full"] = sorted(ful - inc)[:20]
        if pred in KEY_PREDICATES or inc != ful:
            result[pred] = row
    return dict(predicates_compared=len(preds), predicates_differing=differing,
                same_as_full_index=not differing, rows=result)


# ---------------------------------------------------------------- scenario

def run_main_function(srcdir):
    p = subprocess.run([sys.executable, "-c", "import main; print(main.main_function())"],
                       cwd=srcdir, capture_output=True, text=True)
    return p.stdout.strip() or p.stderr.strip().splitlines()[-1]


def xref_hits(db, files, targets, defs):
    """References in `files` whose target is one of `targets`, read from
    python.XRefsViaNameByFile. Each hit names the innermost definition that
    contains it, or None for module level (an import or re-export)."""
    hits = []
    for f in files:
        for r in query(db, 'python.XRefsViaNameByFile { file = "%s" }' % f):
            for x in r["key"]["xrefs"]:
                target = x["target"]["key"]
                if target not in targets:
                    continue
                at = x["source"]["start"]
                inside = [(d["length"], n) for n, d in defs.items() if d["file"] == f
                          and d["start"] <= at < d["start"] + d["length"]]
                hits.append(dict(changed_definition=target, file=f, start=at,
                                 inside=min(inside)[1] if inside else None))
    return hits


def review_sentences(name, flagged, hits, edited, defs, sentence_file="sentences.json"):
    """Join the caller query and the reference query to the saved sentences.
    The verdicts are written by hand in example/descriptions/; nothing here
    judges text. A sentence is selected by callers when the definition it is
    about calls a changed definition. It is selected by references when a
    reference to a changed definition sits inside the definition it is about,
    or at module level in its file when it is about a name that file does not
    define (such as a re-export)."""
    data = json.loads((ROOT / "example" / "descriptions" / sentence_file).read_text())
    why = {}
    for f in flagged:
        if f["caller_file"] and not f["caller_file_edited"]:
            why.setdefault(f["caller"], []).append(f["changed_definition"])
    rows = []
    for s in data["sentences"]:
        if name not in s["verdict_after"]:
            return None
        v = s["verdict_after"][name]
        defined_here = s["about"] in defs and defs[s["about"]]["file"] == s["file"]
        refs = sorted({h["changed_definition"] for h in hits if h["file"] == s["file"] and (
            h["inside"] == s["about"] or (h["inside"] is None and not defined_here))})
        rows.append(dict(id=s["id"], file=s["file"], heading=s["heading"], about=s["about"],
                         text=s["text"], file_edited=s["file"] in edited,
                         selected_because_it_calls=why.get(s["about"], []),
                         selected_because_it_references=refs,
                         still_correct=v["still_correct"], reason=v["reason"]))
    unedited = [r for r in rows if not r["file_edited"]]

    def count(pick):
        sel = [r for r in unedited if pick(r)]
        return dict(selected=len(sel),
                    selected_and_now_wrong=[r["id"] for r in sel if not r["still_correct"]],
                    selected_and_still_correct=[r["id"] for r in sel if r["still_correct"]],
                    not_selected_but_now_wrong=[r["id"] for r in unedited
                                                if not pick(r) and not r["still_correct"]])
    by_calls = count(lambda r: r["selected_because_it_calls"])
    summary = dict(
        sentence_file=sentence_file,
        sentences_in_unedited_files=len(unedited),
        **by_calls,
        callers_or_references=count(lambda r: r["selected_because_it_calls"]
                                    or r["selected_because_it_references"]),
        in_edited_files_and_now_wrong=[r["id"] for r in rows
                                       if r["file_edited"] and not r["still_correct"]])
    return dict(summary=summary, sentences=rows)


def scenario(name, base_dir, new_dir, base_db, mode, label=None):
    old_files, new_files = py_files(base_dir), py_files(new_dir)
    edited = sorted(f for f in old_files | new_files
                    if f not in old_files or f not in new_files
                    or (base_dir / f).read_bytes() != (new_dir / f).read_bytes())
    unedited = sorted((old_files & new_files) - set(edited))
    if mode == "edited_only":
        extra = []
    else:
        extra = importers_of(set(edited), base_dir, new_dir,
                             transitive=(mode == "importers"))
    reindexed = sorted(set(edited) | set(extra))
    excluded = sorted(set(reindexed) & old_files)

    tag = (name if mode == "edited_only" else f"{name}__{mode}") + (f"__{label}" if label else "")
    db, factdir = f"ex/{tag}", ROOT / "facts" / tag
    p = glean("create", "--db", db, "--incremental", base_db, "--exclude", ",".join(excluded),
              check=False)
    if p.returncode != 0:
        # Recorded, not fatal: the June 2024 Glean image aborts when a layer
        # is created on top of another incremental layer.
        err = [l for l in (p.stdout + p.stderr).splitlines() if "Assertion" in l or "rror" in l]
        return dict(scenario=name, mode=mode, label=label, parent_db=base_db, db=db,
                    edited_files=edited, reindexed_files=reindexed,
                    glean_create_failed=dict(exit=p.returncode, lines=err[-3:]))
    indexed = set(reindexed) & new_files
    counts = write_db(db, new_dir, factdir, only=indexed)
    assert set(counts) == indexed, (sorted(counts), sorted(indexed))
    glean("complete", "--db", db)
    glean("finish", "--db", db)

    old = definitions(base_db, base_dir, [f for f in edited if f in old_files])
    new = definitions(db, new_dir, [f for f in edited if f in new_files])
    changed = sorted(n for n in old if n in new and old[n]["text"] != new[n]["text"])
    removed = sorted(n for n in old if n not in new)
    added = sorted(n for n in new if n not in old)

    # Check that the excluded units' old facts are hidden in the incremental
    # database: each re-indexed file should show only its newly written rows.
    rows = {f: dict(parent=len(query(base_db, 'python.DefinitionLocation { file = "%s" }' % f)),
                    incremental=len(query(db, 'python.DefinitionLocation { file = "%s" }' % f)),
                    written=sum(len(b["facts"]) for b in json.loads(
                        (factdir / (f.replace("/", "__") + ".json")).read_text())
                        if b["predicate"].startswith("python.DefinitionLocation")))
            for f in sorted(indexed)}

    after = definitions(db, new_dir)              # the view after the change
    before = definitions(base_db, base_dir)       # the view before it
    flagged = {}
    for callee in changed + removed:
        for which, d in (("before", base_db), ("after", db)):
            for r in query(d, 'python.CalleeToCaller { callee = "%s" }' % callee):
                caller = r["key"]["caller"]["key"]
                where = after.get(caller) or before.get(caller) or {}
                row = flagged.setdefault((callee, caller), dict(
                    changed_definition=callee, caller=caller,
                    caller_file=where.get("file"),
                    caller_file_edited=where.get("file") in edited, found_in=[]))
                row["found_in"].append(which)
    flagged = [flagged[k] for k in sorted(flagged)]
    targets = set(changed + removed)
    hits = []
    for which, d, defs_ in (("before", base_db, before), ("after", db, after)):
        files = [f for f in unedited]
        for h in xref_hits(d, files, targets, defs_):
            h["found_in"] = which
            hits.append(h)
    reread = sorted({f["caller_file"] for f in flagged
                     if f["caller_file"] and not f["caller_file_edited"]})
    sentences_name = name
    return dict(scenario=name, mode=mode, label=label,
                parent=str(base_dir.relative_to(ROOT)), parent_db=base_db,
                changed_dir=str(new_dir.relative_to(ROOT)), db=db,
                edited_files=edited, unedited_files=unedited,
                reindexed_files=reindexed,
                reindexed_but_not_edited=extra,
                facts_written_incrementally=counts,
                definition_rows_in_reindexed_files=rows,
                changed_definitions=changed, removed_definitions=removed,
                added_definitions=added, callers_of_changed_or_removed=flagged,
                unedited_files_whose_description_needs_rereading=reread,
                unedited_files_not_flagged=[f for f in unedited if f not in reread],
                main_function_output=dict(before=run_main_function(base_dir),
                                          after=run_main_function(new_dir)),
                references_to_changed_or_removed_in_unedited_files=hits,
                saved_sentences=review_sentences(sentences_name, flagged, hits, edited, after)
                if name.startswith("variant_") else None,
                saved_sentences_rewritten_after_reexport=review_sentences(
                    sentences_name, flagged, hits, edited, after,
                    "sentences_after_reexport.json")
                if name.startswith("variant_") else None,
                incremental_compared_with_full_index=compare_with_full_index(
                    name, db, new_dir, [ROOT / "facts" / "base", factdir]))


def summary_row(r):
    if "glean_create_failed" in r:
        return dict(scenario=r["scenario"], mode=r["mode"], label=r["label"],
                    parent_db=r["parent_db"], edited=r["edited_files"],
                    reindexed=r["reindexed_files"], glean_create_failed=r["glean_create_failed"])
    cmp = r["incremental_compared_with_full_index"]
    c2c = cmp["rows"].get("python.CalleeToCaller", {})
    s = r["saved_sentences"]["summary"] if r["saved_sentences"] else None
    c = r["saved_sentences_rewritten_after_reexport"]
    c = c["summary"] if c else None
    return dict(scenario=r["scenario"], mode=r["mode"], label=r["label"], parent_db=r["parent_db"],
                edited=r["edited_files"], reindexed=r["reindexed_files"],
                same_as_full_index=cmp["same_as_full_index"],
                predicates_differing=cmp["predicates_differing"],
                calls_missing=c2c.get("only_in_full", []),
                calls_extra=c2c.get("only_in_incremental", []),
                reread=r["unedited_files_whose_description_needs_rereading"],
                main_function=r["main_function_output"],
                sentences_selected_and_now_wrong=s and s["selected_and_now_wrong"],
                sentences_not_selected_but_now_wrong=s and s["not_selected_but_now_wrong"],
                sentences_by_callers_or_references=s and s["callers_or_references"],
                rewritten_sentences=c and dict(
                    by_callers={k: c[k] for k in ("selected", "selected_and_now_wrong",
                                                  "not_selected_but_now_wrong")},
                    by_callers_or_references=c["callers_or_references"],
                    in_edited_files_and_now_wrong=c["in_edited_files_and_now_wrong"]))


def main():
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    ex = ROOT / "example"
    base_dir = ex / "branch_v1"
    both = ("edited_only", "importers")
    try:
        glean("create", "--db", "ex/base")
        base_counts = write_db("ex/base", base_dir, ROOT / "facts" / "base")
        glean("complete", "--db", "ex/base")
        glean("finish", "--db", "ex/base")
        results = []
        for n in ("variant_callee_only", "variant_docstring_only",
                  "variant_reexport", "variant_caller_edited", "branch_v2"):
            for mode in both:
                results.append(scenario(n, base_dir, ex / n, "ex/base", mode))
        # A second edit after the re-export: only lib.calculate_result
        # changes, from * 3 to * 4. First on top of the repaired incremental
        # layer, then on top of a fresh full index of variant_reexport.
        x4 = ("variant_reexport_then_x4", ex / "variant_reexport",
              ex / "variant_reexport_then_x4")
        results.append(scenario(*x4, "ex/variant_reexport__importers", "edited_only",
                                label="on_layer"))
        for mode in both:
            results.append(scenario(*x4, "ex/variant_reexport_full", mode))
        # main.py reaches lib only through utils; calculate_result moves from
        # lib.py to a new core.py, and lib re-exports it.
        glean("create", "--db", "ex/chain_v1")
        write_db("ex/chain_v1", ex / "chain_v1", ROOT / "facts" / "chain_v1")
        glean("complete", "--db", "ex/chain_v1")
        glean("finish", "--db", "ex/chain_v1")
        for mode in ("edited_only", "direct_importers", "importers"):
            results.append(scenario("chain_move", ex / "chain_v1", ex / "chain_move",
                                    "ex/chain_v1", mode))
        report = dict(base_facts=base_counts,
                      predicates_dropped_for_this_glean_schema=sorted(DROPPED),
                      summary=[summary_row(r) for r in results],
                      scenarios=results)
        (out / "report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report["summary"], indent=2))
    finally:
        (out / "glean_commands.log").write_text("\n\n".join(LOG))


if __name__ == "__main__":
    main()
