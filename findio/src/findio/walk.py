"""Iterate the files findio will inspect."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from findio.text import is_binary
from pathspec.gitignore import GitIgnoreSpec
from tree_sitter_language_pack import detect_language

# Trees that are dependencies or build output even when .gitignore forgets them.
# --no-ignore disables this list. .git is always skipped.
_SKIP_DIR_NAMES = frozenset(
    {
        "node_modules",
        "vendor",
        "venv",
        ".venv",
        "dist",
        "build",
        "target",
        "__pycache__",
    }
)

_MAX_FILE_SIZE = 10 * 1024 * 1024
_BINARY_PROBE = 8 * 1024
# Languages read_file extracts. A build directory with one of these is source,
# not compiler output. Data formats the pack still detects stay out.
_SOURCE_LANGUAGES = frozenset(
    {
        "bash",
        "c",
        "cpp",
        "csh",
        "dash",
        "fish",
        "go",
        "java",
        "javascript",
        "kotlin",
        "ksh",
        "php",
        "powershell",
        "python",
        "ruby",
        "rust",
        "sh",
        "shell",
        "tcsh",
        "tsx",
        "typescript",
        "zsh",
    }
)


def walk(*roots: Path, ignore: bool = True) -> Iterator[tuple[Path, Path]]:
    """Yield ``(root, relative)`` for each file under every repository root.

    Each root is walked on its own, with its own ``.gitignore``. ``ignore``
    applies that file and the dependency/build directory list. Files larger
    than 10 MiB are skipped. A NUL in the first 8 KiB skips a file too, except
    UTF-16 and UTF-32 text.
    """
    if not roots:
        raise ValueError("at least one repository root")
    for root in roots:
        resolved = Path(root).resolve()
        if not resolved.is_dir():
            raise NotADirectoryError(resolved)
        yield from _walk_root(resolved, ignore=ignore)


def _walk_root(root: Path, *, ignore: bool) -> Iterator[tuple[Path, Path]]:
    spec = _gitignore(root) if ignore else None
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        dirnames[:] = [
            name
            for name in dirnames
            if not _skip_directory(root, current, name, spec, ignore=ignore)
        ]
        for name in filenames:
            path = current / name
            if not _is_regular_file(path):
                continue
            relative = path.relative_to(root)
            if spec is not None and spec.match_file(relative.as_posix()):
                continue
            if _too_large(path) or _leading_nul(path):
                continue
            yield root, relative


def _gitignore(root: Path) -> GitIgnoreSpec | None:
    path = root / ".gitignore"
    if not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return GitIgnoreSpec.from_lines(lines)


def _skip_directory(
    root: Path,
    current: Path,
    name: str,
    spec: GitIgnoreSpec | None,
    *,
    ignore: bool,
) -> bool:
    if name == ".git":
        return True
    if not ignore:
        return False
    if name in _SKIP_DIR_NAMES:
        # A package may be named build. Output has no source file.
        if name == "build" and _contains_source(current / name):
            return False
        return True
    if spec is None:
        return False
    relative = (current / name).relative_to(root).as_posix()
    return spec.match_file(relative) or spec.match_file(relative + "/")


def _contains_source(path: Path) -> bool:
    """True when this directory itself holds a file findio extracts."""
    try:
        children = list(path.iterdir())
    except OSError:
        return False
    return any(child.is_file() and detect_language(child.name) in _SOURCE_LANGUAGES for child in children)


def _is_regular_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _too_large(path: Path) -> bool:
    return path.stat().st_size > _MAX_FILE_SIZE


def _leading_nul(path: Path) -> bool:
    with path.open("rb") as handle:
        return is_binary(handle.read(_BINARY_PROBE))
