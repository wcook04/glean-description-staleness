# glean-description-staleness

After a commit, [Glean](https://glean.software)'s caller links select the saved descriptions in unedited files that need rereading. In the example here, changing one multiplier in `utils.py` selects three saved sentences about `main.py`, which was left unedited. One of them, "returns twice the value stored in HelperClass", is now false: the function returns 126 for a stored 42.

A description of a file can go wrong when a function it calls changes, even if the file itself was left alone. Glean records who calls what and can update that per commit, so a caller query can choose where to look. Reading the selected sentence is still what decides whether it is wrong.

## What runs

`scripts/run.py` does the following for a base directory and a changed directory of Python files.

1. [Pyrefly](https://pyrefly.org) writes Glean facts for the base. Each source file is one ownership unit.
2. `glean create`, `write`, `complete` and `finish` build a base database.
3. `glean create --incremental <base> --exclude <edited files>` builds an incremental database, and facts for the edited files are written into it. Pyrefly is given only the edited files to check; it still reads the others to resolve imports.
4. Each definition in an edited file is compared, old text against new text. Nested definitions are blanked out first, so a class counts as changed only when its own lines changed.
5. For each changed or removed definition, the incremental database is queried with `python.CalleeToCaller { callee = "<name>" }`.
6. Saved sentences about a caller in an unedited file are selected for rereading.
7. The changed directory is also indexed from scratch, and the answers of the two databases are compared by content for six predicates.

The workflow in `.github/workflows/run.yml` runs this on GitHub Actions with the published image `ghcr.io/facebookincubator/glean/demo:latest` and Pyrefly 1.4.1. The databases live inside the job and are discarded afterwards. The full output is the `out/report.json` artifact of each run.

## The example

`example/branch_v1` and `example/branch_v2` are copied from Glean's tests (`glean/lang/codemarkup/tests/python/branches_cases`, licence in `example/LICENSE-Glean`). Each has `main.py`, `lib.py` and `utils.py`.

Four variants of `branch_v1` were made for this test. Each differs from it only as stated.

| Variant | What differs from `branch_v1` |
|---|---|
| `variant_callee_only` | `utils.calculate_result` returns `value * 3` where it returned `value * 2`. |
| `variant_docstring_only` | Only the docstring of `utils.calculate_result`. |
| `variant_reexport` | `calculate_result` moves to `lib.py` (multiplying by three) and `utils.py` re-exports it. `main.py` still imports it from `utils`. |
| `variant_caller_edited` | `main.main_function` no longer calls `calculate_result`. |

`example/descriptions/sentences.json` holds six sentences written for `branch_v1` under the headings CodeMap uses for file descriptions (`INTERFACE`, `FLOW`, `DEPENDENCIES`). They were constructed for this test, and each carries a hand-written verdict for each variant. They are an illustration of the mechanism. They were not found stale in a real project's history.

## Result

Sentences in unedited files, per variant:

| Variant | Files edited | Caller Glean returned | Sentences selected | Selected and now false | Selected and still true | False but left unselected |
|---|---|---|---|---|---|---|
| `variant_callee_only` | `utils.py` | `main.main_function` | 3 of 5 | 1 | 2 | 0 |
| `variant_docstring_only` | `utils.py` | `main.main_function` | 3 of 5 | 0 | 3 | 0 |
| `variant_reexport` | `utils.py`, `lib.py` | `main.main_function` | 3 of 4 | 2 | 1 | 0 |
| `variant_caller_edited` | `main.py` | none | 0 of 2 | 0 | 0 | 0 |

The three selected sentences are the ones about `main.main_function`. For `variant_callee_only`:

| Saved sentence | After the change |
|---|---|
| "main_function(): returns twice the value stored in HelperClass." | False. It returned 84 before and returns 126 after, for a stored 42. |
| "main_function reads the value from a HelperClass and passes it to calculate_result." | Still true. |
| "Couples: calculate_result is defined in utils.py." | Still true. |

All three share one call relationship. Which of them is false depends on their wording, so the query chooses where to read and a reader decides.

The docstring-only variant selects the same three sentences and none is false, because step 4 compares definition text.

### Incremental database compared with a fresh full index

| Variant | Same answers for all six predicates |
|---|---|
| `variant_callee_only` | yes |
| `variant_docstring_only` | yes |
| `variant_caller_edited` | yes |
| `branch_v2` | yes |
| `variant_reexport` | no |

In `variant_reexport` the fresh index contains `main.main_function calls lib.calculate_result`. The incremental database lacks it, because `main.py` was unedited and its facts were kept from the base, where the import resolved to `utils.calculate_result`. The file was still selected here, through the removed definition `utils.calculate_result`. A later change to `lib.calculate_result` would miss it. Choosing which unedited files to index again after a change like this is outside what this script does.

## Limits

- The example has three files and the sentences were constructed for it. Nothing here measures how often selected descriptions are false, or how many false ones go unselected, on a real codebase.
- The selection covers sentences in unedited files. Sentences in an edited file are expected to be updated with that edit, and this test does not check that they were.
- Only direct callers are queried. Callers of callers are not followed, and neither are dependencies on constants, types or data formats.
- An unselected sentence has only been passed over by this rule. It has not been checked.
- The published Glean image dates from June 2024. Its Python schema does not define two predicates that Pyrefly 1.4.1 writes, `python.XRefsViaNameByTarget` and `python.xrefs.XRefsByFile`. The script leaves those two out. `python.CalleeToCaller` is unaffected.
