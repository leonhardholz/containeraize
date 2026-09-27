# findio

findio scans one or more repositories and reports the files that are likely to perform I/O, and the APIs or commands that made them look that way. It is a filter. A later step can read those files and turn the findings into runtime dependencies.

The scan walks each root, skips gitignored and vendored trees, and parses the rest with tree-sitter. Each language extractor records imports and the calls rooted on them. Shell, PHP, C, C++, and PowerShell also record bare commands. A name declared in any scanned root is dropped, so a later model call is not spent on the repository's own functions. Several roots share that declaration set.

What remains is classified as a symbol, once per language, module, and name. A fixed table answers the well-known APIs. The SQLite cache answers anything already labeled. The rest go to a model through [LiteLLM](https://github.com/BerriAI/litellm). `ai.py` is the only caller. It uses `litellm.completion()` with a `provider/model` string from `FINDIO_MODEL`, so LiteLLM picks the provider and talks to it directly. There is no proxy. Credentials stay in that provider's own variables (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and so on). `FINDIO_API_BASE` is sent only when a custom endpoint is needed. The prompt contains symbol names, never source. Symbols of one language are sent in batches of 30, one completion at a time, and each result is stored on its own. A static hit is not sent and is not cached. When `FINDIO_MODEL` is unset, or a completion cannot be used, the report still comes from the table and the cache, and unlabeled symbols stay not I/O.

Each label is `io` plus zero or more kinds. Kinds are a hint for the later step. A wrong or incomplete kind is acceptable.

| Kind | Meaning |
| --- | --- |
| `filesystem` | Reading or writing files, such as `open` or `os.remove`. |
| `network` | Generic network I/O, such as sockets, HTTP, DNS, or `curl`. |
| `service` | A client for one named external service, such as AWS S3, used instead of `network` for that client. |
| `process` | Starting another program, such as `subprocess.run`. |
| `database` | Talking to a database, such as `sqlite3.connect`. |

The implementation contract is in [docs/design.md](docs/design.md).

## Install

Python 3.11 or newer.

```sh
pip install -e ".[dev]"
```

## Scan

```sh
findio /path/to/repo
findio /path/to/left /path/to/right --output report.txt
findio /path/to/repo --format json
```

Without `FINDIO_MODEL`, the report uses a built-in table of known I/O APIs. With it set to a LiteLLM model string, unknown symbols are classified by that model and cached.

```sh
export FINDIO_MODEL=gemini/gemini-2.5-flash
findio /path/to/repo
```

The provider's own environment variables hold the credentials. `FINDIO_API_BASE` overrides the endpoint when set. Labels are stored in `$XDG_CACHE_HOME/findio/classifications.sqlite`.

| Option | |
| --- | --- |
| `--format text\|json` | Report format. Default `text`. |
| `--output PATH` | Write the report to a file. Default is stdout. |
| `--jobs N` | Worker processes. Default is the available cores. |
| `--cache PATH` | Classification cache. |
| `--refresh` | Ask the model again and overwrite cached labels. |
| `--no-ignore` | Do not apply `.gitignore` or the dependency and build skips. |

Progress is printed on stderr: `walk`, `walk N`, `parse`, `filter N`, `classify`, `report N`.

## Report

Text lists each file, then the symbols found there. The kinds are the ones in the introduction.

```text
src/client.py
  network  python  requests.get  12,40
  filesystem  python  pathlib.Path.read_text  18
```

JSON has the same groups. One root stores that path in `root`. Several roots join the absolute paths with a newline, and each file path is prefixed with the root directory name.

## Languages

Imports and the calls on them: Python, JavaScript, TypeScript, TSX, Go, Rust, Java, Kotlin, Ruby, C#.

Bare commands as well: shell, PHP, C, C++, PowerShell.

Other detected languages, including Dockerfiles and YAML, contribute no symbols.

## Library

```python
from pathlib import Path
from findio import scan

report = scan(Path("/path/to/repo"))
print(report.to_text())
```

`scan(client=None)` always uses the static table, even when `FINDIO_MODEL` is set. The CLI builds the model cascade.

## Tests

```sh
pytest -q
```
