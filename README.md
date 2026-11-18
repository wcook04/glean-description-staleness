# glean-description-staleness

After a commit, [Glean](https://glean.software)'s incremental index and caller links pick out the unedited files whose saved descriptions need rereading. In the example here, changing one function in `utils.py` marks `main.py`, which calls it, and leaves `lib.py` alone.

A description of a file can go wrong when a function it calls changes, even if the file itself was left alone. Glean already records who calls what and updates that per commit, so the same query selects the descriptions to check.

## What runs

`scripts/run.py` does the following for a base directory and a changed directory of Python files.

1. [Pyrefly](https://pyrefly.org) writes Glean facts for the base. Each source file is one ownership unit.
2. `glean create`, `write`, `complete` and `finish` build a base database.
3. `glean create --incremental <base> --exclude <edited files>` builds an incremental database. Only the edited files are indexed again.
4. Each definition in an edited file is compared, old text against new text. Nested definitions are blanked out first, so a class counts as changed only when its own lines changed.
5. For each changed or removed definition, the incremental database is queried with `python.CalleeToCaller { callee = "<name>" }`.
6. A caller in a file that was left unedited marks that file's description for rereading.

The workflow in `.github/workflows/run.yml` runs this on GitHub Actions with the published image `ghcr.io/facebookincubator/glean/demo:latest` and Pyrefly 1.4.1. The full output is the `out/report.json` artifact of each run.

## The example

`example/branch_v1` and `example/branch_v2` are copied from Glean's tests (`glean/lang/codemarkup/tests/python/branches_cases`, licence in `example/LICENSE-Glean`). Each has `main.py`, `lib.py` and `utils.py`.

`example/variant_callee_only` was made for this test. It is `branch_v1` with one change: `utils.calculate_result` returns `value * 3` where it returned `value * 2`. `main.py` and `lib.py` are byte-identical to `branch_v1`.

## Result

| Change | Files edited | Changed definitions | Callers found by Glean | Unedited files to reread | Unedited files left alone |
|---|---|---|---|---|---|
| `branch_v1` to `variant_callee_only` | `utils.py` | `utils.calculate_result` | `main.main_function` | `main.py` | `lib.py` |
| `branch_v1` to `branch_v2` | all three | 10 changed, 6 added | 4, all in `main.py` | none | none |

In the first row, 32 facts were written for `utils.py`. The caller link from `main.main_function` comes from the base database, which was not written again.

In each incremental database, the edited files show only their newly written definition rows (for example 5 for `utils.py` in the first row), so the old facts for those files are hidden.

In the second row every file was edited, so there is no unedited file whose description could have gone wrong.

## Limits

- The example has three files. Nothing here measures how many flagged descriptions are actually wrong on a real codebase.
- The published Glean image dates from June 2024. Its Python schema does not define two predicates that Pyrefly 1.4.1 writes, `python.XRefsViaNameByTarget` and `python.xrefs.XRefsByFile`. The script leaves those two out. `python.CalleeToCaller` is unaffected.
- Only direct callers are queried. Callers of callers are not followed.
- A caller link shows that a description might be affected. Whether the description is still correct needs a reader.
