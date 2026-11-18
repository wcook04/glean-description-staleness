# glean-description-staleness

When a function is edited, a sentence describing one of its callers can become false even though the caller's file was not edited. This repository uses [Glean](https://glean.software) to find those callers. After a change, a script asks Glean which functions call each changed function, and lists the saved sentences about those callers so that a person knows which ones to reread.

It runs on the three-file Python example from Glean's own tests. In one test a single function is edited so that it multiplies by three instead of two. Glean returns its one caller, which is in a file that was not edited. Three sentences had been saved about that caller, and one of them, "returns twice the value stored in HelperClass", is now false.

## What a saved description is

I ask coding agents to write a few sentences about each file, under fixed headings in the file's docstring, while they still have the context from writing the code. When an agent edits a file it updates that file's sentences. It does not update the sentences in other files, and those are the ones this test is about.

For this test the sentences are in `example/descriptions/sentences.json`. There are six, written for the first version of the example under three of the headings (`INTERFACE`, `FLOW`, `DEPENDENCIES`). Each one has a hand-written verdict, true or false, for each change below. They were written to show the mechanism. They do not come from a real project's history.

## The example

`example/branch_v1` and `example/branch_v2` are copied from `glean/lang/codemarkup/tests/python/branches_cases` in Glean (licence in `example/LICENSE-Glean`). Each has three files.

- `lib.py` defines `HelperClass`, which stores the value 42, and `helper_function`.
- `utils.py` defines `calculate_result(value)`, which returns `value * 2`.
- `main.py` imports from both. Its `main_function` reads the value from a `HelperClass` and passes it to `calculate_result`, so it returns 84.

In `branch_v2` all three files are edited, so it has no unedited file to test. I made four smaller changes to `branch_v1`, each in its own directory.

| Directory | The change |
|---|---|
| `variant_callee_only` | `calculate_result` returns `value * 3`. |
| `variant_docstring_only` | Only the docstring of `calculate_result` is edited. |
| `variant_reexport` | `calculate_result` is moved into `lib.py`, where it returns `value * 3`, and `utils.py` now contains `from lib import calculate_result`. `main.py` is not edited and still imports it from `utils`. |
| `variant_caller_edited` | `main_function` no longer calls `calculate_result`. |

## What the script does

`scripts/run.py` runs these steps. Every `glean` command below also takes `--db-root` and `--schema`, left out here to keep the lines short.

1. **Index the first version.** [Pyrefly](https://pyrefly.org) writes Glean facts as JSON, one file of facts per source file. The script adds `"unit": "<source file name>"` to every batch, so each source file is one ownership unit. Then:

   ```
   glean create --db ex/base
   glean write --db ex/base facts/base/*.json
   glean complete --db ex/base
   glean finish --db ex/base
   ```

2. **Index a change incrementally.** The edited files are the ones whose bytes differ from the first version. Pyrefly is run on those files only (it still reads the others to resolve imports), and their facts are written into a new database on top of the base:

   ```
   glean create --db ex/variant_callee_only --incremental ex/base --exclude utils.py
   glean write --db ex/variant_callee_only facts/variant_callee_only/utils.py.json
   glean complete --db ex/variant_callee_only
   glean finish --db ex/variant_callee_only
   ```

3. **Find the definitions whose text changed.** `python.DefinitionLocation { file = "utils.py" }` is queried on both databases, which gives the span of each function, class and module-level variable. The script compares the source text in each span, old against new. Nested definitions are blanked out first, so a class counts as changed only when its own lines changed.

4. **Ask for the callers.** For each changed or removed definition the incremental database is queried with `python.CalleeToCaller { callee = "utils.calculate_result" }`. Pyrefly writes these caller facts itself, in the batch for the caller's file. No `glean derive` step is run.

5. **List the sentences.** If a returned caller is in a file that was not edited, the saved sentences about that caller are listed for rereading.

6. **Compare with a full index.** The changed directory is also indexed from scratch into a third database. The facts in the incremental database and the full one are compared by content, ignoring fact ids, for `python.CalleeToCaller`, `python.DefinitionLocation`, `python.DeclarationLocation`, `python.FileCall`, `python.XRefsViaNameByFile` and `src.File`.

## Results

### One function edited (`variant_callee_only`)

Only `utils.py` is edited. Glean returns one caller of `utils.calculate_result`, which is `main.main_function`, from the facts for `main.py` that are still in the base database. `main_function` returned 84 before the change and returns 126 after it.

Three saved sentences are about `main_function`:

| Saved sentence | After the change |
|---|---|
| "main_function(): returns twice the value stored in HelperClass." | False. It returns three times the value. |
| "main_function reads the value from a HelperClass and passes it to calculate_result." | Still true. |
| "Couples: calculate_result is defined in utils.py." | Still true. |

All three sentences are about the same call, so the caller query lists all three. Finding the false one still takes reading them. The two sentences that are not listed, one about another function in `main.py` and one about `lib.py`, are both still true.

### Only a docstring edited (`variant_docstring_only`)

The same three sentences are listed and all three are still true. Step 3 compares the whole text of a definition, including its docstring, so this edit counts as a change.

### A function moved to another file (`variant_reexport`)

In this case the incremental database ends up missing a caller fact that a full index has.

`utils.py` and `lib.py` are edited and `main.py` is not. The three sentences about `main_function` are still listed, because `utils.calculate_result` no longer exists as a definition and the facts for `main.py`, kept from the base, still have `main_function` as its caller. Two of the three sentences are now false.

The full index contains the fact that `main.main_function` calls `lib.calculate_result`. The incremental database does not. `main.py` was not edited, so its facts were not written again, but the function its import resolves to has changed. If `lib.calculate_result` were edited next, a caller query on this database would return no caller in `main.py`.

The script indexes only the edited files again. It has no step that finds unedited files whose facts need writing again because a file they import from was edited.

### The caller itself edited (`variant_caller_edited`)

Only `main.py` is edited, and no sentence in an unedited file is listed. The sentences about `main_function` are in the edited file. The agent that edits `main.py` is expected to update them, and the script does not examine whether it did.

### Counts

Sentences in unedited files, for each change:

| Change | Listed for rereading | Listed and now false | Listed and still true | False and not listed |
|---|---|---|---|---|
| `variant_callee_only` | 3 of 5 | 1 | 2 | 0 |
| `variant_docstring_only` | 3 of 5 | 0 | 3 | 0 |
| `variant_reexport` | 3 of 4 | 2 | 1 | 0 |
| `variant_caller_edited` | 0 of 2 | 0 | 0 | 0 |

For `variant_callee_only`, `variant_docstring_only`, `variant_caller_edited` and `branch_v2`, the incremental database and the full index contain the same facts for all six predicates. For `variant_reexport` they differ as described above.

## What this does not show

- The example has three files and six sentences written for it. It gives no rate for how often a listed sentence is false in a real codebase, or for how many false sentences go unlisted.
- Only direct callers are queried. A sentence can also depend on a function several calls away, or on a constant, a type or a data format, and none of those are followed.
- No program or person has examined a sentence that is not listed.
- Sentences in an edited file are left to the agent that made the edit.

## Running it

`.github/workflows/run.yml` runs the script on every push, on a GitHub Actions `ubuntu-latest` runner. It uses the published image `ghcr.io/facebookincubator/glean/demo:latest` and Pyrefly 1.4.1. The databases are created inside the container and discarded when the job ends. The full output of each run is the `out/report.json` artifact, and `out/glean_commands.log` has every Glean command with its output.

The published image was built in June 2024. Its Python schema does not define two predicates that Pyrefly 1.4.1 writes, `python.XRefsViaNameByTarget` and `python.xrefs.XRefsByFile`, so the script leaves those two out before writing. `python.CalleeToCaller` is in that schema.
