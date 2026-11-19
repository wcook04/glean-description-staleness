# glean-description-staleness

When a function is edited, a sentence describing one of its callers can become false even though the caller's file was not edited. This repository uses [Glean](https://glean.software) to find those callers. After a change, a script asks Glean which functions call each changed function, and lists the saved sentences about those callers so that a person knows which ones to reread.

It runs on the three-file Python example from Glean's own tests. In one test a single function is edited so that it multiplies by three instead of two. Glean returns its one caller, which is in a file that was not edited. Three sentences had been saved about that caller, and one of them, "returns twice the value stored in HelperClass", is now false.

Two more results come from the same example. When a function moves to another file, the incremental database matches a fresh index only if the unedited files that import an edited file, directly or through other files, are indexed again as well. And a saved sentence about a re-export becomes false without its file calling anything, so the script also selects sentences by Glean's references, which lists that sentence and no extra true one.

## What a saved description is

I ask coding agents to write a few sentences about each file, under fixed headings in the file's docstring, while they still have the context from writing the code. When an agent edits a file it updates that file's sentences. It does not update the sentences in other files, and those are the ones this test is about.

For this test the sentences are in `example/descriptions/sentences.json`. There are six, written for the first version of the example under three of the headings (`INTERFACE`, `FLOW`, `DEPENDENCIES`). Each one has a hand-written verdict, true or false, for each change below. They were written to show the mechanism. They do not come from a real project's history.

## The example

`example/branch_v1` and `example/branch_v2` are copied from `glean/lang/codemarkup/tests/python/branches_cases` in Glean (licence in `example/LICENSE-Glean`). Each has three files.

- `lib.py` defines `HelperClass`, which stores the value 42, and `helper_function`.
- `utils.py` defines `calculate_result(value)`, which returns `value * 2`.
- `main.py` imports from both. Its `main_function` reads the value from a `HelperClass` and passes it to `calculate_result`, so it returns 84.

In `branch_v2` all three files are edited, so it has no unedited file to test. I made four smaller changes to `branch_v1`, each in its own directory, and two more cases.

| Directory | The change |
|---|---|
| `variant_callee_only` | `calculate_result` returns `value * 3`. |
| `variant_docstring_only` | Only the docstring of `calculate_result` is edited. |
| `variant_reexport` | `calculate_result` is moved into `lib.py`, where it returns `value * 3`, and `utils.py` now contains `from lib import calculate_result`. `main.py` is not edited and still imports it from `utils`. |
| `variant_caller_edited` | `main_function` no longer calls `calculate_result`. |
| `variant_reexport_then_x4` | A second change, made to `variant_reexport`: only `lib.calculate_result` is edited, to return `value * 4`. |
| `chain_v1` → `chain_move` | A separate pair. In `chain_v1`, `main.py` imports everything from `utils`, which re-exports it from `lib`, so `main.py` imports nothing from `lib` directly. In `chain_move`, `calculate_result` moves from `lib.py` to a new `core.py` and `lib.py` re-exports it. `utils.py` and `main.py` are not edited. |

## What the script does

`scripts/run.py` runs these steps. Every `glean` command below also takes `--db-root` and `--schema`, left out here to keep the lines short.

1. **Index the first version.** [Pyrefly](https://pyrefly.org) writes Glean facts as JSON, one file of facts per source file. The script adds `"unit": "<source file name>"` to every batch, so each source file is one ownership unit. Then:

   ```
   glean create --db ex/base
   glean write --db ex/base facts/base/*.json
   glean complete --db ex/base
   glean finish --db ex/base
   ```

2. **Index a change incrementally.** The edited files are the ones whose bytes differ from the first version. The script keeps a second list, the files to index again, and runs each change in two ways:

   - **edited files only**: the second list is the edited files;
   - **with importers**: the second list also has every unedited file that imports an edited file, in the old or the new version, directly or through other files. The imports are read from the source with Python's `ast` module, which stands in here for the dependency list a build system would give. The two-hop case is also run with **direct importers** only.

   Pyrefly is run on the files in the second list only (it still reads the others to resolve imports), and their units are excluded from the base. A Pyrefly run that fails, or that writes facts for fewer files than it was given, stops the script. For edited files only:

   ```
   glean create --db ex/variant_callee_only --incremental ex/base --exclude utils.py
   glean write --db ex/variant_callee_only facts/variant_callee_only/utils.py.json
   glean complete --db ex/variant_callee_only
   glean finish --db ex/variant_callee_only
   ```

3. **Find the definitions whose text changed.** `python.DefinitionLocation { file = "utils.py" }` is queried on both databases, which gives the span of each function, class and module-level variable. The script compares the source text in each span, old against new. Nested definitions are blanked out first, so a class counts as changed only when its own lines changed.

4. **Ask for the callers.** For each changed or removed definition the base and the incremental database are both queried with `python.CalleeToCaller { callee = "utils.calculate_result" }`. Pyrefly writes these caller facts itself, in the batch for the caller's file. No `glean derive` step is run.

5. **List the sentences.** If a returned caller is in a file that was not edited, the saved sentences about that caller are listed for rereading. This uses the edited list, not the list of files indexed again.

6. **Compare with a full index.** The changed directory is also indexed from scratch into a third database. The facts in the incremental database and the full one are compared by content, ignoring fact ids, for every predicate either one was given: fourteen, including `python.CalleeToCaller`, `python.XRefsViaNameByFile` and `python.NameToSName`.

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

With edited files only, nothing finds unedited files whose facts need writing again because a file they import from was edited.

With importers, `main.py` is indexed again because it imports `utils.py` and `lib.py`. The incremental database then has the same facts as the full index for all fourteen predicates, including the fact that `main.main_function` calls `lib.calculate_result`. The same three sentences are listed.

### A second change on top of the move (`variant_reexport_then_x4`)

Only `lib.py` is edited, and `main_function` now returns 168. The parent database is a full index of `variant_reexport`, which has the same facts as the repaired incremental database above.

With edited files only, `main.py` keeps its facts from the parent and they still name `lib.calculate_result` as the function it calls. The incremental database has the same facts as a full index for all fourteen predicates, and the caller query lists the three sentences about `main_function`. So once the facts for `main.py` are right, a later change to the function's body does not need `main.py` indexed again, while its sentences still need rereading.

`utils.py` was not edited either. Its sentence "calculate_result(value): returns value multiplied by two" is false, and was already false after the move. It is not listed: `utils.py` now only imports and re-exports the function, and an import is not a call, so the caller query does not return it.

To test that without a sentence that was already wrong, `example/descriptions/sentences_after_reexport.json` has seven sentences rewritten so that every one is true for `variant_reexport`. The one for `utils.py` is "Re-exports calculate_result from lib.py, which returns value multiplied by three." After the change to `* 4`, three of them are false: one about `main_function`, the one for `utils.py`, and one in `lib.py`, which was edited.

The script also selects sentences a second way. It reads `python.XRefsViaNameByFile` for the unedited files and takes every reference whose target is a changed definition. A sentence is selected when such a reference is inside the definition it is about, or when the reference is at module level (an import) and the sentence is about a name its file does not define, such as a re-export.

| Selected by | Sentences in unedited files listed | Listed and now false | Listed and still true | False and not listed |
|---|---|---|---|---|
| Callers | 3 of 5 | 1 (`main_function` returns three times) | 2 | 1 (the `utils.py` re-export) |
| Callers or references | 4 of 5 | 2 | 2 | 0 |

The reference in `utils.py` is the import line `from lib import calculate_result`. No true sentence was added to the list. With the original sentences the two ways give the same lists for every other change.

I first built this change on top of the repaired incremental database itself, as a second layer. `glean create --incremental` exited with an assertion in `glean/rts/ownership/slice.cpp` (``base.empty() || first >= base.end()``). The report keeps that run. This is the June 2024 image; I have not checked whether a newer Glean behaves differently.

### Two imports away (`chain_move`)

`core.py` is new and `lib.py` is edited. `utils.py` imports from `lib.py`, and `main.py` imports from `utils.py`.

| Files indexed again | Same as full index | Caller facts for `main_function` |
|---|---|---|
| edited only: `core.py`, `lib.py` | No | still says it calls `lib.calculate_result`; the call to `core.calculate_result` is missing |
| direct importers: adds `utils.py` | No | the same |
| all importers: adds `utils.py` and `main.py` | Yes, all fourteen predicates | calls `core.calculate_result` |

Here the database is wrong in both directions until `main.py` is indexed again: it keeps a call that no longer happens and lacks the one that does. Following imports one step is not enough.

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
| `variant_reexport_then_x4` | 3 of 5 | 2 | 1 | 1 |

The counts are the same with edited files only and with importers.

With importers, every case gives the same facts as the full index for all fourteen predicates. With edited files only, `variant_reexport` and `chain_move` differ, in `python.CalleeToCaller` and `python.XRefsViaNameByFile`; every other case matches.

## What this does not show

- The example has three files and six sentences written for it. It gives no rate for how often a listed sentence is false in a real codebase, or for how many false sentences go unlisted.
- The list of importers comes from reading `import` lines with Python's `ast` module on three or four files. A real project would take it from its build system or type checker, and would also need relative imports, search paths and modules that cannot be resolved handled. Indexing too many files again is safe; leaving one out is not.
- With importers, every file that imports an edited file is indexed again, including after a change, such as `variant_callee_only`, where its facts would have stayed right. No smaller list is tried.
- Only direct callers and direct references are queried. A sentence can also depend on a function several calls away, or on a constant, a type or a data format, and none of those are followed.
- No program or person has examined a sentence that is not listed.
- Sentences in an edited file are left to the agent that made the edit.

## Running it

`.github/workflows/run.yml` runs the script on every push, on a GitHub Actions `ubuntu-latest` runner. It uses the published image `ghcr.io/facebookincubator/glean/demo:latest` and Pyrefly 1.4.1. The databases are created inside the container and discarded when the job ends. The full output of each run is the `out/report.json` artifact, and `out/glean_commands.log` has every Glean command with its output.

The published image was built in June 2024. Its Python schema does not define two predicates that Pyrefly 1.4.1 writes, `python.XRefsViaNameByTarget` and `python.xrefs.XRefsByFile`, so the script leaves those two out before writing. `python.CalleeToCaller` is in that schema.
