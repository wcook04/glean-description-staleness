# glean-description-staleness

I ask coding agents to write a few sentences about each file in the file's docstring, and to update them whenever they edit that file. The problem: editing a function in one file can make a sentence in a *different* file false, and the agent editing the first file never touches the second.

This repository tests whether [Glean](https://glean.software) can find those sentences. After each change it updates a Glean database incrementally, asks Glean which functions call or refer to the changed code, and lists the sentences about those functions for rereading. Every incremental database is checked against a fresh index of the same files.

The results, on Glean's own three-file Python example:

1. **Re-indexing only the edited files can leave another file's facts wrong.** When a function moves to another file, an unedited file that imports it keeps a call to the old location. Indexing again every unedited file that imports an edited file, following imports through other files, made the incremental database identical to a fresh index in every case tested.
2. **The files to index again and the sentences to reread are different lists.** After a later change to the function's body, the unedited caller's Glean facts stay correct, but a sentence about it becomes false.
3. **Callers alone miss sentences about re-exports.** A file that only imports and re-exports a function has no call to it. Adding Glean's references to the search finds that sentence.

The sentences and their true or false verdicts were written by hand for this test. This shows the mechanism on three files, not how often it happens in a real project.

## The example

`example/branch_v1` is copied from Glean's test fixture [`glean/lang/codemarkup/tests/python/branches_cases`](https://github.com/facebookincubator/Glean/tree/main/glean/lang/codemarkup/tests/python/branches_cases) (licence in `example/LICENSE-Glean`). The parts that matter:

```python
# utils.py
def calculate_result(value):
    return value * 2

# lib.py
class HelperClass:
    def __init__(self):
        self.value = 42
    ...

# main.py
from lib import helper_function, HelperClass
from utils import calculate_result

def main_function():
    helper = HelperClass()
    result = helper.get_value()
    calculated = calculate_result(result)
    return calculated          # 42 * 2 = 84
```

Each other directory under `example/` is a copy of a starting version with one change:

| Directory | Starting version | What changes | Files edited |
|---|---|---|---|
| `variant_callee_only` | `branch_v1` | `calculate_result` returns `value * 3` | `utils.py` |
| `variant_docstring_only` | `branch_v1` | only the docstring of `calculate_result` | `utils.py` |
| `variant_reexport` | `branch_v1` | `calculate_result` moves to `lib.py` (returning `value * 3`), and `utils.py` gets the line `from lib import calculate_result` so `main.py` can still import it from `utils` | `utils.py`, `lib.py` |
| `variant_caller_edited` | `branch_v1` | `main_function` stops calling `calculate_result` | `main.py` |
| `variant_reexport_then_x4` | `variant_reexport` | `lib.calculate_result` returns `value * 4` | `lib.py` |
| `chain_move` | `chain_v1` | see below | `lib.py`, new `core.py` |

`chain_v1` is a second starting version where `main.py` imports only from `utils.py`, and `utils.py` imports `calculate_result` from `lib.py` (`from lib import calculate_result, helper_function, HelperClass`). In `chain_move`, `calculate_result` moves from `lib.py` to a new file `core.py`, and `lib.py` gets the line `from core import calculate_result`. So `main.py` reaches the function through two imports, and neither `main.py` nor `utils.py` is edited.

## What the script does

`scripts/run.py`, run by `.github/workflows/run.yml` on every push:

1. **Write facts.** [Pyrefly](https://pyrefly.org) 1.4.1 indexes each version and writes Glean facts as JSON, one batch per source file. The script tags each batch with `"unit": "<file name>"`, so each file is one Glean ownership unit.
2. **Build the base database** from the starting version: `glean create`, `glean write`, `glean complete`, `glean finish`.
3. **Build an incremental database for the change.** The script works out two lists:
   - *edited files*: files whose bytes differ between the two versions;
   - *files to index again*: depending on the mode, either the edited files only, or the edited files plus every unedited file that imports one of them (read from the `import` lines with Python's `ast` module, in both versions, following imports through other files). For `chain_move` there is also a mode that follows only one import.

   Then `glean create --db <new> --incremental <base> --exclude <files to index again>`, and the new facts for those files are written in.
4. **Find the changed definitions.** For each edited file, the script reads the span of every function and class from `python.DefinitionLocation` in both databases and compares the source text inside each span.
5. **Find what uses them**, in both the old and new database:
   - *callers*: `python.CalleeToCaller { callee = "<changed definition>" }`;
   - *references*: entries in `python.XRefsViaNameByFile` for unedited files whose target is a changed definition. This includes import lines.
6. **List sentences to reread.** A sentence in an unedited file is listed if the function it describes calls a changed definition, or (references mode) contains a reference to it, or if the sentence is about a re-export and its file has an import of it.
7. **Check the database.** The new version is also indexed from scratch, and every fact in the two databases is compared after removing fact ids. That covers all 14 predicates Pyrefly writes that this Glean schema has, including `python.CalleeToCaller`, `python.XRefsViaNameByFile`, `python.DefinitionLocation` and `python.NameToSName`.

## Results

All numbers are from [GitHub Actions run 44513979679](https://github.com/wcook04/glean-description-staleness/actions/runs/44513979679). Its `out/report.json` artifact has a `summary` list with one row per case and mode, and `out/glean_commands.log` has every Glean command with its output.

### Does the incremental database match a fresh index?

"Matches" means: the same version was also indexed from scratch into a separate database, the two databases were compared record by record (every call, reference, definition and name Glean stored, ignoring Glean's internal record ids), and they were identical.

| Change | Index edited files again | Also index every file that imports them |
|---|---|---|
| `variant_callee_only` | matches | matches |
| `variant_docstring_only` | matches | matches |
| `variant_reexport` | **no**: lacks the call `main_function` → `lib.calculate_result` | matches |
| `variant_caller_edited` | matches | matches |
| `branch_v2` (all files edited) | matches | matches |
| `variant_reexport_then_x4` | matches | matches |
| `chain_move` | **no**: keeps `main_function` → `lib.calculate_result`, lacks `main_function` → `core.calculate_result` | matches |

For `chain_move` the script also tried indexing again only the files that import an edited file directly, which adds `utils.py` but not `main.py`. The database still said `main_function` calls `lib.calculate_result` and still lacked the call to `core.calculate_result`, exactly as with edited files only.

In `variant_reexport`, `main.py` was not edited, so with edited files only its facts are kept from the base database. Those facts were written when `calculate_result` lived in `utils.py`. A fresh index of the new version records that `main_function` now calls `lib.calculate_result`; the incremental database does not. Indexing `main.py` again fixes it.

In `chain_move`, indexing `utils.py` again (it imports the edited `lib.py` directly) is not enough. `main.py`, two imports away, also has to be indexed again.

Indexing every importer is more than strictly needed. In `variant_callee_only`, `main.py` is indexed again and Pyrefly writes exactly the same facts for it as before, byte for byte.

### Which sentences are listed for rereading?

The sentences are in `example/descriptions/sentences.json`, written for `branch_v1`, each with a hand-written verdict for every change. For example, for `variant_callee_only` (`* 2` becomes `* 3`), Glean returns one caller of `utils.calculate_result`, `main.main_function`, in the unedited `main.py`. Three sentences describe it:

| Sentence about `main_function` | After `* 3` |
|---|---|
| "main_function(): returns twice the value stored in HelperClass." | false: it now returns 126, three times 42 |
| "main_function reads the value from a HelperClass and passes it to calculate_result." | true |
| "Couples: calculate_result is defined in utils.py." | true |

Glean picks the right function, but a person or model still has to read all three sentences to find the false one.

Sentences in unedited files, using callers:

| Change | Listed | Listed and now false | Listed and still true | False but not listed |
|---|---|---|---|---|
| `variant_callee_only` | 3 of 5 | 1 | 2 | 0 |
| `variant_docstring_only` | 3 of 5 | 0 | 3 | 0 |
| `variant_reexport` | 3 of 4 | 2 | 1 | 0 |
| `variant_caller_edited` | 0 of 2 | 0 | 0 | 0 |

In `variant_docstring_only` the three sentences are listed even though nothing they say changed, because the script compares a function's whole text, docstring included.

### Re-indexing not needed, rereading needed (`variant_reexport_then_x4`)

Starting from `variant_reexport`, only `lib.py` changes: `calculate_result` returns `value * 4`, so `main_function` returns 168 instead of 126. The base database here is a fresh index of `variant_reexport`.

With edited files only, `main.py` keeps its facts from the base, and they already say `main_function` calls `lib.calculate_result`. The incremental database matches a fresh index. So `main.py` did not need indexing again, but its sentence "returns three times the value stored in HelperClass" is now false.

To test this with sentences that were all true before the change, `example/descriptions/sentences_after_reexport.json` has seven sentences written for `variant_reexport`. One is about `utils.py`: "Re-exports calculate_result from lib.py, which returns value multiplied by three." After `* 4`, three sentences are false: one about `main_function`, the one about `utils.py`, and one in the edited `lib.py`.

| Listed using | Listed (of 5 in unedited files) | Listed and now false | Listed and still true | False but not listed |
|---|---|---|---|---|
| callers | 3 | 1 | 2 | 1, the `utils.py` sentence |
| callers and references | 4 | 2 | 2 | 0 |

`utils.py` contains no call to `calculate_result`, only the line `from lib import calculate_result`, so a caller query never returns it. Glean's references include that import line, which lists the sentence. No sentence that was still true was added.

### Stacking a second incremental database

I first tried the `* 4` change as an incremental database built on top of the repaired incremental database for `variant_reexport`, rather than on a fresh index:

```
glean create --db ex/variant_reexport_then_x4__on_layer --incremental ex/variant_reexport__importers --exclude lib.py
```

It exited with code 134 and `glean/rts/ownership/slice.cpp:51: ... Assertion 'base.empty() || first >= base.end()' failed`. Glean's documentation describes stacking incremental databases, so this is a failure to investigate, not a missing feature. I have not reduced it to a smaller case or tried a Glean newer than this image.

## What this does not show

- How often any of this happens in a real codebase. Three files, a handful of sentences, verdicts written by hand.
- That reading `import` lines is a correct dependency list for real Python. It handles only the plain absolute and relative imports in these files. A real project would take the list from its build system, and would need search paths, imports that cannot be resolved, and new files that change what an existing import resolves to.
- The smallest list of files to index again. Every importer is indexed again, even when its facts cannot change.
- Sentences that depend on code more than one call or reference away, or on a constant, a type or a data format.
- Whether a sentence that is not listed is still true. Nothing reads those.
- Whether stacked incremental databases work, since that run failed.

## Running it

The workflow installs Pyrefly 1.4.1, starts the published image `ghcr.io/facebookincubator/glean/demo:latest` (built June 2024, Linux x86 only), and runs `python3 scripts/run.py`, which calls the `glean` binary inside the container with `docker exec`. To run it yourself on a Linux x86 machine with Docker:

```sh
pip install pyrefly==1.4.1
docker run -d --name glean -v "$PWD:/work" --entrypoint sleep ghcr.io/facebookincubator/glean/demo:latest infinity
python3 scripts/run.py
```

The image's Python schema does not define two predicates Pyrefly 1.4.1 writes, `python.XRefsViaNameByTarget` and `python.xrefs.XRefsByFile`, so the script drops them before writing.
