# repotree engineering design

repotree scans one or more repositories and prints a structural abstract. Package manifests are named. Build and deployment files are named. Every other file is a count by language, or `other`. A directory is shown in full only when it contains a manifest or leads to one. Every other directory is one summary line.

This document is the implementation contract for `~/containeraize/repotree`. There is no model and no tree-sitter parse. The languages are the ones named in findio's README.

## Decisions

- Python package, library first, thin CLI on top. The parent tool calls `repotree.scan()`.
- A manifest is a filename on a fixed list. The list covers the languages findio extracts. Lockfiles, `requirements.txt`, and `DESCRIPTION` are not manifests. A `.psd1` file is a manifest only when its first 32 KiB contains `ModuleVersion`, `RootModule`, or `ModuleToProcess`.
- A build or deployment file is either a filename on a second fixed list, or a hit from a text probe of the first 32 KiB. The probe is a line scan. It does not parse YAML or JSON, and it does not expand the directory.
- A manifest wins over a build name. A build name wins over the probe.
- Files that are neither are counted by the final suffix. The suffix map uses findio's language names. Anything else is `other`.
- The root of each scan is always expanded. A directory that contains a manifest, and every ancestor of that directory, is expanded too. An expanded directory lists every subdirectory. A directory with no manifest in its subtree is one line and does not name the directories inside it.
- Gitignored paths are omitted. Dependency directories are recorded as skipped and are not walked. `.git` is omitted. Symlinks are not followed.
- One scan of several roots returns one report. Each root is its own tree. Paths inside a tree stay relative to that root.

## Pipeline

```mermaid
flowchart LR
  walk[Walk files] --> class[Classify each file]
  class --> spine[Mark the manifest spine]
  spine --> roll[Roll up other trees]
  roll --> report[Text or JSON]
```

1. Walk each root.
2. Classify each visited file as a manifest, a build file, or a language count.
3. Mark the root, every manifest directory, and every ancestor of a manifest directory as expanded.
4. Roll every other directory into one summary.
5. Write the report.

Stderr prints `walk` when the walk starts and `walk N` when it finishes. `N` is the number of files classified. Files inside an omitted or skipped directory are not in that count.

## Layout

```
repotree/
  pyproject.toml
  README.md
  docs/design.md
  src/repotree/
    __init__.py           # scan
    cli.py
    walk.py
    markers.py            # manifest names and language suffixes
    buildfiles.py         # build filenames and the text probe
    summary.py            # spine and rollup
    report.py
  tests/
    test_markers.py
    test_build.py
    test_walk.py
    test_scan.py
```

The package targets Python 3.11+. The only dependency is `pathspec`.

## Walking files

`walk.py` collects the directories under one repository root. The key `""` is the root. Each directory records the files directly inside it, the child directories that were entered, and the child directories that were skipped.

Skip, always:

- `.git`
- symbolic links to files or directories

Skip when `ignore` is true, and do not record them:

- paths matched by the root `.gitignore`, read with `pathspec`. Nested gitignore files are not read. A directory matches either its relative path or that path with a trailing slash.

Skip when `ignore` is true, record the directory name on its parent, and do not enter it:

- `node_modules`, `vendor`, `venv`, `.venv`, `dist`, `target`, `__pycache__`

`--no-ignore` disables gitignore and this list. `.git` and symbolic links stay omitted. A directory named `build` is walked like any other directory.

Large files and files that contain a NUL are still classified. The probe below is the only reader, and it stops at 32 KiB or at a NUL.

Paths in the report are relative POSIX paths. Several roots do not prefix those paths. A repeated root path is scanned once.

## Manifests

`markers.py`. Match is by filename, except `.psd1`, which is read. Exact names are case-sensitive. Suffixes are not.

| Language | Files |
| --- | --- |
| Python | `pyproject.toml`, `setup.py`, `setup.cfg`, `Pipfile` |
| JavaScript, TypeScript, TSX | `package.json`, `pnpm-workspace.yaml` |
| Go | `go.mod`, `go.work` |
| Rust | `Cargo.toml` |
| Java, Kotlin | `pom.xml`, `build.gradle`, `build.gradle.kts`, `settings.gradle`, `settings.gradle.kts` |
| C# | `*.csproj`, `*.sln`, `*.slnx` |
| Ruby | `Gemfile`, `*.gemspec` |
| PHP | `composer.json` |
| C, C++ | `CMakeLists.txt`, `meson.build` |
| PowerShell | `*.psd1` |
| Shell | none |

A `.psd1` file whose text contains `ModuleVersion`, `RootModule`, or `ModuleToProcess` is a manifest, so it is not also counted as PowerShell. Any other `.psd1` file is PowerShell. Shell has no manifest, so a shell tree collapses to language counts.

## Languages

The label is the final suffix, lowercased. A leading-dot name with no further dot, such as `.gitignore`, is `other`. `Button.test.tsx` is TSX. `archive.tar.gz` is `other`.

| Suffix | Label |
| --- | --- |
| `.py`, `.pyi` | Python |
| `.js`, `.mjs`, `.cjs`, `.jsx` | JavaScript |
| `.ts`, `.mts`, `.cts` | TypeScript |
| `.tsx` | TSX |
| `.go` | Go |
| `.rs` | Rust |
| `.java` | Java |
| `.kt`, `.kts` | Kotlin |
| `.cs` | C# |
| `.rb` | Ruby |
| `.php` | PHP |
| `.c`, `.h` | C |
| `.cpp`, `.cc`, `.cxx`, `.hpp`, `.hh`, `.hxx` | C++ |
| `.ps1`, `.psm1`, `.psd1` | PowerShell |
| `.sh`, `.bash`, `.zsh`, `.fish`, `.ksh` | shell |

`.h` is C. The tool does not parse the file to choose C++.

## Build and deployment files

`buildfiles.py`. A named file is a build file without reading it. Otherwise the first 32 KiB are read. A NUL in that prefix, or a file that cannot be opened, skips the probe and the file is counted by suffix. Markdown (`.md`, `.markdown`, `.mdown`, `.rst`) is never probed, so a document that quotes a `FROM` line stays `other`.

Named files, case-sensitive except the suffixes:

- Make: `Makefile`, `makefile`, `GNUmakefile`, `BSDmakefile`, `Makefile.am`, `makefile.am`, `Makefile.in`, `makefile.in`, `*.mk`, `*.mak`, `*.make`
- Docker image: `Dockerfile`, `Dockerfile.*`, `Containerfile`, `Containerfile.*`, `*.dockerfile`
- Docker Compose: `compose.yml`, `compose.yaml`, `docker-compose.yml`, `docker-compose.yaml`
- Task runners: `Justfile`, `justfile`, `Taskfile.yml`, `Taskfile.yaml`, `Rakefile`
- CI by name: `Jenkinsfile`, `Jenkinsfile.*`, `.gitlab-ci.yml`, `azure-pipelines.yml`, `.travis.yml`, `appveyor.yml`, `.drone.yml`, `.circleci/config.yml` (that relative path only), `cloudbuild.yaml`, `cloudbuild.yml`
- Delivery by name: `buildspec.yml`, `appspec.yml`, `Procfile`, `fly.toml`, `render.yaml`, `serverless.yml`, `serverless.yaml`, `skaffold.yaml`, `skaffold.yml`, `Chart.yaml`, `Chart.yml`, `kustomization.yaml`, `kustomization.yml`, `Pulumi.yaml`, `Pulumi.yml`, `Vagrantfile`, `ansible.cfg`
- Suffix: `*.tf`, `*.tf.json`, `*.tfvars`, `*.bicep`, `*.pkr.hcl`, `*.pkr.json`, `*.nomad`, `*.pp`, `*.sls`

The probe runs only when the name did not match. For `.yml` and `.yaml`, the first hit wins:

1. CloudFormation: `AWSTemplateFormatVersion`, `Transform` with `AWS::Serverless`, or `Type` with `AWS::`.
2. GitHub workflow: both `on` and `jobs` are top-level keys. A key is top-level when its line has no leading space and is not a comment or a document marker (`---` or `...`). Quotes around the key are allowed.
3. Ansible: a `hosts` key and one of `tasks`, `roles`, `pre_tasks`, or `handlers`. Those keys may be indented. A role task file that has no `hosts` key stays `other`.
4. Docker Compose: top-level `services`, and an `image` or `build` key.
5. Kubernetes: both `apiVersion` and `kind`.

For `.json`, the first hit wins:

1. CloudFormation, same strings as above.
2. Azure ARM: the text contains `deploymentTemplate.json`, or a `type` whose value starts with `Microsoft.`.
3. Kubernetes, same keys as above.

Any other probed file is a Docker image when it has a `FROM` line and one of `RUN`, `COPY`, `ADD`, `CMD`, `ENTRYPOINT`, or `WORKDIR` at the start of a line. Otherwise it is a Makefile when it has `.PHONY` or `.SUFFIXES`, a `define` followed by a name and closed by `endef`, or `ifeq`, `ifdef`, `ifneq`, or `ifndef` followed by an operand. A target line followed by a tab-indented recipe is not enough. A bare `ifdef` line is not enough.

A build file does not make its directory expanded. Inside a collapsed directory, each build file is printed by its path relative to that directory, and it is left out of the language counts.

## Collapse

`summary.py`.

The expanded set is the root, every directory that directly contains a manifest, and every ancestor of those directories. An expanded directory lists:

- its manifest filenames, sorted
- its build filenames, sorted
- one count line for its other direct files
- each skipped child, sorted, as `name/  skipped`
- every entered child, sorted by name

A child in the expanded set is printed as its own directory. Any other child is one line covering that whole subtree. The line names the build files found anywhere under it, then the file count, the language counts, the number of directories under it, and the depth of the longest directory chain. A skipped directory inside that subtree counts as one directory and does not contribute its contents. A count of zero files is omitted. A count of zero directories is omitted. Depth is printed only when the directory count is not zero.

Language counts are ordered by count descending, then by name. The separator in text is `×`.

## Report

Version 1. The library returns dataclasses. `to_json()` and `to_text()` write the same tree.

```json
{
  "version": 1,
  "roots": [
    {
      "path": "/abs/path/to/repo",
      "name": "",
      "tree": {
        "path": "",
        "markers": ["package.json"],
        "build": ["Dockerfile", "Makefile"],
        "files": {"other": 2},
        "file_count": 2,
        "skipped": ["node_modules"],
        "collapsed": [
          {
            "path": "src",
            "build": [],
            "files": {"TypeScript": 2, "TSX": 1},
            "file_count": 3,
            "directories": 2,
            "depth": 1
          }
        ],
        "children": []
      }
    }
  ]
}
```

`path` on a node is relative to that root. `build` on an expanded node is basenames. `build` on a collapsed node is relative to the collapsed directory. `file_count` excludes manifests and build files. `name` on a root is empty when one root is scanned. With several roots it is the directory name, and a repeated name becomes `name-2` for the later root.

Text with one root has no heading. Text with several roots prints the absolute path on its own line before each tree, and a blank line between trees.

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

`packages/` is expanded because `api` and `web` contain manifests, so both are listed, and so is every other child of `packages/`. `src/` has no manifest, so `components/` is not named.

## CLI

```
repotree <path> [<path> ...] [--format text|json] [--output FILE] [--no-ignore]
```

The default format is text. `--output` writes the report instead of stdout. Stderr is the two walk lines, not a second summary.

## Library

```python
def scan(*roots: Path, ignore: bool = True) -> Report:
    ...
```

`ignore` defaults to true. `Report.to_text()` and `Report.to_json()` render the result. A scan with no roots raises `ValueError`. A root that is not a directory raises `NotADirectoryError`.

## Tests

Snippets are written into a temporary tree.

- Each findio language's manifest is recognized, and a lockfile is not. Suffixes map to the language names above, and Markdown, a leading-dot name, and a compound archive suffix are `other`.
- A named build file is recognized without readable content. A manifest wins when the same file would also match a probe.
- CloudFormation matches in YAML and JSON. Azure ARM matches in JSON. A GitHub workflow matches under any name when `on` and `jobs` are top-level, and not when `on` is nested. An Ansible playbook matches, and a role task file without `hosts` does not. Kubernetes and Compose match. A Dockerfile and a Makefile match by content. Markdown and a NUL prefix do not.
- Gitignore, `.git`, symlinks, and the dependency list are omitted or marked skipped. A directory named `build` is walked, including a manifest inside it. `--no-ignore` walks `node_modules` and counts it.
- A `.psd1` file with `ModuleVersion` is a manifest. A `.psd1` file without a module-manifest key is PowerShell. `*.slnx` is a manifest. A Go function, a NEON mapping, and a bare target-and-recipe are not Makefiles. `ifndef` is.
- A fixture tree names manifests and build files, collapses `src/` without naming `components/`, and leaves a build file inside a collapsed directory. Two roots print absolute headings and keep paths relative. The JSON report has version 1. The CLI writes JSON to `--output` and leaves stdout empty.
