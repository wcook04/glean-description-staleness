#!/usr/bin/env python3
"""Run Pyrefly over a directory of Python files and write Glean JSON facts,
one file per source file, with each batch owned by a unit named after the file.

Glean's incremental mode hides and replaces facts by unit, so the unit is the
grain at which a later commit can replace part of the database.
"""
import argparse, json, pathlib, subprocess, tempfile

ap = argparse.ArgumentParser()
ap.add_argument("srcdir")
ap.add_argument("outdir")
ap.add_argument("--only", default="", help="comma-separated source files to keep")
ap.add_argument("--keep-schema-id", action="store_true")
a = ap.parse_args()

only = {f for f in a.only.split(",") if f}
out = pathlib.Path(a.outdir).resolve()
out.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    subprocess.run(
        ["pyrefly", "check", "--check-all", f"--report-glean={tmp}/output",
         "--output-format=omit-errors", "--summary=none", "./**/*.py",
         "--search-path=."],
        cwd=a.srcdir, check=False)
    for f in sorted(pathlib.Path(tmp, "output").glob("*.json")):
        batches = json.loads(f.read_text())
        src = next(b["facts"][0]["key"]["file"]["key"] for b in batches
                   if b.get("predicate", "").startswith("src.FileLanguage"))
        if only and src not in only:
            continue
        kept = []
        for b in batches:
            if "predicate" not in b:
                if a.keep_schema_id:
                    kept.append(b)
                continue
            if not b["facts"]:
                continue
            b["unit"] = src
            kept.append(b)
        (out / (src.replace("/", "__") + ".json")).write_text(json.dumps(kept, indent=1))
        print(f"{src}: {sum(len(b['facts']) for b in kept if 'facts' in b)} facts, unit {src}")
