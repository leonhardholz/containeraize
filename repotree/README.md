# repotree

repotree scans one or more repositories and prints a structural abstract. Package manifests are named. Build and deployment files are named. Every other file is a count by language, or `other`. A directory is shown in full only when it contains a manifest or leads to one. Every other directory is one summary line.

There is no model and no tree-sitter parse. The languages are the ones findio extracts. The implementation contract is in [docs/design.md](docs/design.md).

## Install

Python 3.11 or newer.

```sh
pip install -e ".[dev]"
```

## Scan

```sh
repotree /path/to/repo
repotree /path/to/left /path/to/right --output report.txt
repotree /path/to/repo --format json
```

| Option | |
| --- | --- |
| `--format text\|json` | Report format. Default `text`. |
| `--output PATH` | Write the report to a file. Default is stdout. |
| `--no-ignore` | Do not apply `.gitignore` or the dependency directory skip list. |

Progress is printed on stderr: `walk`, then `walk N`. `N` is the number of files classified.

The walk omits `.git` and symbolic links. With ignore left on, it also omits paths matched by the root `.gitignore`, and it records these directories as skipped without entering them: `node_modules`, `vendor`, `venv`, `.venv`, `dist`, `target`, `__pycache__`.

## Report

Text with one root has no heading. Manifests and build files are listed by name. Other files in that directory are one count line, ordered by count, then by name. A directory with no manifest anywhere under it is one line: build files found inside it, then the file count, the language counts, the number of directories, and the depth. A skipped directory is `name/  skipped`.

```text
package.json
Dockerfile
Makefile
other ×2
node_modules/  skipped
.github/  workflows/deploy.yml  1 dir  depth 1
src/  3 files  TypeScript ×2  TSX ×1  2 dirs  depth 1
packages/
  api/
    pom.xml
    src/  1 file  Java ×1  2 dirs  depth 2
  web/
    package.json
    src/  2 files  TSX ×1  TypeScript ×1
```

`packages/` is expanded because `api` and `web` contain manifests, so both are listed. `src/` has no manifest, so the directories inside it are not named.

Several roots print the absolute path on its own line before each tree, with a blank line between trees. Paths inside a tree stay relative to that root. JSON is the same tree, version 1. `name` is empty for one root. With several roots it is the directory name, and a repeated name becomes `name-2`.

## Languages

Counts use the final suffix. Anything else, including a leading-dot name such as `.gitignore`, is `other`.

Python, JavaScript, TypeScript, TSX, Go, Rust, Java, Kotlin, Ruby, C#, shell, PHP, C, C++, PowerShell.

Manifests for those languages, and the build and deployment files named by filename or by a short text probe, are listed in [docs/design.md](docs/design.md). A manifest is not also counted in the language line. A build file does not expand its directory.

## Library

```python
from pathlib import Path
from repotree import scan

report = scan(Path("/path/to/repo"))
print(report.to_text())
```

`scan(*roots, ignore=True)` returns a `Report`. `to_text()` and `to_json()` render it. A scan with no roots raises `ValueError`. A root that is not a directory raises `NotADirectoryError`.

## Tests

```sh
pytest -q
```
