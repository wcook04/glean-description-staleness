#!/usr/bin/env python3
"""After a change, ask Glean which definitions call the changed ones, and list
the files whose saved descriptions therefore need rereading.

Steps, for a base directory and a changed directory of Python files:
  1. Pyrefly writes Glean facts for the base; each source file is one unit.
  2. Glean builds a base database from those facts.
  3. Glean builds an incremental database on top: the changed files' units are
     excluded and only those files are indexed again.
  4. Definitions in the changed files are compared, old text against new text.
  5. For each changed or removed definition, the incremental database is asked
     for its callers (python.CalleeToCaller).
  6. Callers in files that were not edited are the descriptions to reread.

The glean binary runs in a container (GLEAN_CONTAINER); this script runs on the
host and calls it with `docker exec`.
"""
import json, os, pathlib, re, subprocess, sys, tempfile

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


def pyrefly_facts(srcdir, outdir, only=None):
    """Write one JSON fact file per source file, owned by a unit named after it."""
    outdir.mkdir(parents=True, exist_ok=True)
    counts = {}
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["pyrefly", "check", "--check-all", f"--report-glean={tmp}/output",
             "--output-format=omit-errors", "--summary=none", "./**/*.py",
             "--search-path=."], cwd=srcdir, check=False)
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


def scenario(name, base_dir, new_dir, base_db):
    old_files = {p.name for p in base_dir.glob("*.py")}
    new_files = {p.name for p in new_dir.glob("*.py")}
    edited = sorted(f for f in old_files | new_files
                    if f not in old_files or f not in new_files
                    or (base_dir / f).read_bytes() != (new_dir / f).read_bytes())
    unedited = sorted((old_files & new_files) - set(edited))
    db = f"ex/{name}"
    glean("create", "--db", db, "--incremental", base_db, "--exclude", ",".join(edited))
    counts = write_db(db, new_dir, ROOT / "facts" / name, only=set(edited) & new_files)
    glean("complete", "--db", db)
    glean("finish", "--db", db)

    old = definitions(base_db, base_dir, [f for f in edited if f in old_files])
    new = definitions(db, new_dir, [f for f in edited if f in new_files])
    changed = sorted(n for n in old if n in new and old[n]["text"] != new[n]["text"])
    removed = sorted(n for n in old if n not in new)
    added = sorted(n for n in new if n not in old)

    # Check that the excluded units' old facts are hidden in the incremental
    # database: each edited file should show only its newly written rows.
    rows = {f: dict(base=len(query(base_db, 'python.DefinitionLocation { file = "%s" }' % f)),
                    incremental=len(query(db, 'python.DefinitionLocation { file = "%s" }' % f)),
                    written=sum(len(b["facts"]) for b in json.loads(
                        (ROOT / "facts" / name / (f + ".json")).read_text())
                        if b["predicate"].startswith("python.DefinitionLocation")))
            for f in edited if f in new_files}

    everything = definitions(db, new_dir)          # the view after the change
    flagged = []
    for callee in changed + removed:
        for r in query(db, 'python.CalleeToCaller { callee = "%s" }' % callee):
            caller = r["key"]["caller"]["key"]
            where = everything.get(caller, {})
            flagged.append(dict(changed_definition=callee, caller=caller,
                                caller_file=where.get("file"),
                                caller_file_edited=where.get("file") in edited))
    reread = sorted({f["caller_file"] for f in flagged
                     if f["caller_file"] and not f["caller_file_edited"]})
    return dict(scenario=name, base=str(base_dir.relative_to(ROOT)),
                changed_dir=str(new_dir.relative_to(ROOT)),
                edited_files=edited, unedited_files=unedited,
                facts_written_incrementally=counts,
                definition_rows_in_edited_files=rows,
                changed_definitions=changed, removed_definitions=removed,
                added_definitions=added, callers_of_changed_or_removed=flagged,
                unedited_files_whose_description_needs_rereading=reread,
                unedited_files_not_flagged=[f for f in unedited if f not in reread])


def main():
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    base_dir = ROOT / "example" / "branch_v1"
    try:
        glean("create", "--db", "ex/base")
        base_counts = write_db("ex/base", base_dir, ROOT / "facts" / "base")
        glean("complete", "--db", "ex/base")
        glean("finish", "--db", "ex/base")
        results = [scenario(n, base_dir, ROOT / "example" / n, "ex/base")
                   for n in ("variant_callee_only", "branch_v2")]
        report = dict(base_facts=base_counts,
                      predicates_dropped_for_this_glean_schema=sorted(DROPPED),
                      scenarios=results)
        (out / "report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
    finally:
        (out / "glean_commands.log").write_text("\n\n".join(LOG))


if __name__ == "__main__":
    main()
