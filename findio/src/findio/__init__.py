"""Find files in a repository that are likely to perform I/O."""

from __future__ import annotations

import sys
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Protocol

from findio.classify import StaticClassifier
from findio.detect import detect
from findio.extract import Definitions, Occurrence, read_file
from findio.report import Label, Location, Report, Symbol, SymbolGroup, symbol_sort_key
from findio.walk import walk

__all__ = [
    "Cache",
    "Classifier",
    "Label",
    "Location",
    "Report",
    "StaticClassifier",
    "Symbol",
    "SymbolGroup",
    "scan",
]


class Classifier(Protocol):
    def classify(self, symbols: list[Symbol]) -> list[Label]:
        """Label each symbol as I/O or not, in the same order."""


class Cache(Protocol):
    """Per-symbol classification cache. Not used by the static classifier."""


def scan(
    *roots: Path,
    client: Classifier | None = None,
    cache: Cache | None = None,
    ignore: bool = True,
    jobs: int = 1,
) -> Report:
    """Walk each root and return one I/O report.

    Declarations from every root are one set, so a symbol defined in any of
    them is internal to all of them. ``client`` defaults to the static
    classifier. A client that classifies further owns its cache; this function
    does not open one. ``jobs`` is the number of worker processes that detect
    and extract files. Stderr gets one line per phase: ``walk``, ``parse``,
    ``filter``, ``classify``, and ``report``. ``walk`` is printed when the
    directory walk starts, and ``walk N`` when the file list is ready. Each
    parsed file is printed during ``parse`` as ``path language``.
    """
    if not roots:
        raise ValueError("at least one repository root")
    if jobs < 1:
        raise ValueError("jobs must be at least 1")
    if client is None:
        client = StaticClassifier()

    _log("walk")
    found_files = list(walk(*roots, ignore=ignore))
    _log(f"walk {len(found_files)}")
    _log("parse")
    resolved = [Path(root).resolve() for root in roots]
    prefixes = _path_prefixes(resolved)
    tasks = (
        (str(root), relative.as_posix(), _report_path(root, relative, prefixes))
        for root, relative in found_files
    )
    if jobs == 1 or len(found_files) < 2:
        inspected: Iterable[tuple[str, str, list[Occurrence], Definitions] | None] = map(_inspect, tasks)
        found = _collect(inspected)
    else:
        workers = min(jobs, len(found_files))
        with ProcessPoolExecutor(max_workers=workers) as pool:
            inspected = pool.map(_inspect, tasks, chunksize=_chunksize(len(found_files), workers))
            found = _collect(inspected)
    visible = _without_internal(found)
    _log(f"filter {_symbol_count(visible)}")
    _log("classify")
    report = _report(_joined_root(resolved), visible, client)
    _log(f"report {len(report.symbols)}")
    return report


def _inspect(item: tuple[str, str, str]) -> tuple[str, str, list[Occurrence], Definitions] | None:
    root_key, relative, report_path = item
    root = Path(root_key)
    path = root / relative
    language = detect(path)
    if language is None:
        return None
    occurrences, defined = read_file(path, language, root)
    return report_path, language, occurrences, defined


def _collect(
    inspected: Iterable[tuple[str, str, list[Occurrence], Definitions] | None],
) -> list[tuple[str, str, list[Occurrence], Definitions]]:
    found: list[tuple[str, str, list[Occurrence], Definitions]] = []
    for item in inspected:
        if item is None:
            continue
        relative, language, occurrences, defined = item
        _log(f"{relative} {language}")
        found.append((relative, language, occurrences, defined))
    return found


def _path_prefixes(roots: list[Path]) -> dict[Path, str]:
    """Directory name for each root when several are scanned, else empty.

    One root keeps report paths relative to that root. Two directories named
    the same get ``name-2`` for the later one.
    """
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    if len(unique) < 2:
        return {root: "" for root in unique}
    used: dict[str, int] = {}
    prefixes: dict[Path, str] = {}
    for root in unique:
        name = root.name
        seen = used.get(name, 0)
        used[name] = seen + 1
        prefixes[root] = name if seen == 0 else f"{name}-{seen + 1}"
    return prefixes


def _report_path(root: Path, relative: Path, prefixes: dict[Path, str]) -> str:
    prefix = prefixes.get(root, root.name)
    relative_path = relative.as_posix()
    return f"{prefix}/{relative_path}" if prefix else relative_path


def _joined_root(roots: list[Path]) -> str:
    if len(roots) == 1:
        return str(roots[0])
    return "\n".join(str(root) for root in roots)


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _symbol_count(found: list[tuple[str, str, list[Occurrence]]]) -> int:
    return len(
        {
            (language, item.module, item.symbol)
            for _path, language, occurrences in found
            for item in occurrences
        }
    )


# C and C++ share function names. Java and Kotlin share packages. JavaScript
# dialects share package names. PowerShell names are compared case-insensitively.
# Ruby method names share one set, and Ruby classes share their declared methods.
_FUNCTION_GROUP = {
    "c": "c",
    "cpp": "c",
    "php": "php",
    "shell": "shell",
    "powershell": "powershell",
    "ruby": "ruby",
}
_SCOPE_GROUP = {
    "java": "jvm",
    "kotlin": "jvm",
    "javascript": "js",
    "typescript": "js",
    "tsx": "js",
    "go": "go",
    "rust": "rust",
    "ruby": "ruby",
    "c": "c",
    "cpp": "c",
    "csharp": "csharp",
}


def _without_internal(
    found: list[tuple[str, str, list[Occurrence], Definitions]],
) -> list[tuple[str, str, list[Occurrence]]]:
    functions: dict[str, set[str]] = {}
    scopes: dict[str, set[str]] = {}
    aliases: set[tuple[str, str]] = set()
    for _path, language, _occurrences, defined in found:
        function_group = _FUNCTION_GROUP.get(language)
        # A shell function is visible only in its file and in files that source it.
        if function_group is not None and language != "shell":
            functions.setdefault(function_group, set()).update(defined.functions)
        scope_group = _SCOPE_GROUP.get(language)
        if scope_group is not None:
            scopes.setdefault(scope_group, set()).update(defined.scopes)
        aliases.update(defined.aliases)
    if aliases and "rust" in scopes:
        _rust_alias_scopes(scopes["rust"], aliases)
    shell_functions = _shell_visible(found)
    visible: list[tuple[str, str, list[Occurrence]]] = []
    for path, language, occurrences, _defined in found:
        if language == "shell":
            function_names = shell_functions.get(path, ())
        else:
            function_names = functions.get(_FUNCTION_GROUP.get(language, ""), ())
        scope_names = scopes.get(_SCOPE_GROUP.get(language, ""), ())
        if function_names or scope_names:
            occurrences = [
                item
                for item in occurrences
                if _keep(language, item, function_names, scope_names)
            ]
        visible.append((path, language, occurrences))
    return visible


def _shell_visible(
    found: list[tuple[str, str, list[Occurrence], Definitions]],
) -> dict[str, set[str]]:
    """Function names each shell file can run: its own, plus those of sourced files."""
    own: dict[str, set[str]] = {}
    edges: dict[str, set[str]] = {}
    files: set[str] = set()
    for path, language, _occurrences, defined in found:
        if language != "shell":
            continue
        files.add(path)
        own[path] = set(defined.functions)
        edges[path] = set()
    for path, language, _occurrences, defined in found:
        if language != "shell":
            continue
        directory = Path(path).parent.as_posix()
        if directory == ".":
            directory = ""
        for raw in defined.sources:
            target = _sourced_file(directory, raw, files)
            if target is not None:
                edges[path].add(target)
    visible = {path: set(names) for path, names in own.items()}
    changed = True
    while changed:
        changed = False
        for path, targets in edges.items():
            for target in targets:
                added = visible[target] - visible[path]
                if added:
                    visible[path].update(added)
                    changed = True
    return visible


def _sourced_file(directory: str, raw: str, files: set[str]) -> str | None:
    raw = raw.strip()
    if not raw or raw.startswith(("/", "~")):
        return None
    parts = [part for part in directory.split("/") if part]
    for part in raw.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        if "/" in part or "\\" in part:
            return None
        parts.append(part)
    candidate = "/".join(parts)
    if candidate in files:
        return candidate
    return None


def _rust_alias_scopes(scopes: set[str], aliases: set[tuple[str, str]]) -> None:
    """Copy ``grep_cli``'s items onto ``grep::cli`` after every crate is known."""
    snapshot = list(scopes)
    for public, target in aliases:
        member = f"{target}\0"
        nested = f"{target}::"
        for key in snapshot:
            if key.startswith(member):
                scopes.add(f"{public}\0{key[len(member):]}")
            elif key.startswith(nested):
                scopes.add(f"{public}::{key[len(nested):]}")


def _keep(language: str, item: Occurrence, functions: set[str] | tuple, scopes: set[str] | tuple) -> bool:
    if not item.module and not item.skips_function and _defined_key(language, item.symbol) in functions:
        return False
    if item.owner and f"{item.owner}\0{item.symbol}" in scopes:
        return False
    if language == "go" and _go_value(item.module, item.symbol, scopes):
        return False
    return not _in_scope(item.module, item.symbol, scopes)


def _go_value(module: str, symbol: str, scopes: set[str] | tuple) -> bool:
    """True when the first selector is a package-level variable or constant.

    ``diagnostics.Hello.Code`` is internal when ``Hello`` is declared in that
    package. ``internal.Read`` stays when ``Read`` is not.
    """
    head, dot, _rest = symbol.partition(".")
    return bool(dot) and bool(module) and f"{module}\0{head}" in scopes


def _in_scope(module: str, symbol: str, scopes: set[str] | tuple) -> bool:
    """True when this exact type or member is declared, not merely its package.

    An import of a member is the qualified name with an empty symbol. A call on
    that member keeps the member as the module. ``Foo.hashCode`` stays when
    ``hashCode`` is not declared on ``Foo``.
    """
    if not module:
        return False
    if not symbol and module in scopes:
        return True
    if symbol and (
        f"{module}\0{symbol}" in scopes
        or f"{module}.{symbol}" in scopes
        or f"{module}.Companion\0{symbol}" in scopes
    ):
        return True
    parent, dot, name = module.rpartition(".")
    return bool(dot) and f"{parent}\0{name}" in scopes


def _defined_key(language: str, symbol: str) -> str:
    if language == "powershell":
        return symbol.casefold()
    return symbol


def _chunksize(file_count: int, jobs: int) -> int:
    return max(1, min(32, file_count // (jobs * 4)))


def _report(
    root: str,
    found: list[tuple[str, str, list[Occurrence]]],
    client: Classifier,
) -> Report:
    unique: list[Symbol] = []
    seen: set[tuple[str, str, str]] = set()
    for _path, language, occurrences in found:
        for occurrence in occurrences:
            key = (language, occurrence.module, occurrence.symbol)
            if key not in seen:
                seen.add(key)
                unique.append(Symbol(*key))
    labels = client.classify(unique) if unique else []
    label_for = dict(zip(unique, labels, strict=True))

    grouped: dict[tuple[str, str, str], dict[str, list[int]]] = {}
    kinds_for: dict[tuple[str, str, str], tuple[str, ...]] = {}
    for path, language, occurrences in found:
        for occurrence in occurrences:
            key = (language, occurrence.module, occurrence.symbol)
            label = label_for[Symbol(*key)]
            if not label.io:
                continue
            kinds_for[key] = label.kinds
            grouped.setdefault(key, {}).setdefault(path, []).append(occurrence.line)

    symbols: list[SymbolGroup] = []
    for (language, module, symbol), paths in grouped.items():
        locations = tuple(
            Location(path, tuple(sorted(set(lines)))) for path, lines in sorted(paths.items())
        )
        symbols.append(
            SymbolGroup(language, module, symbol, kinds_for[(language, module, symbol)], locations)
        )
    symbols.sort(key=symbol_sort_key)
    return Report(version=3, root=root, symbols=tuple(symbols))
