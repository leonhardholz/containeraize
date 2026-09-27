"""Walk a repository, skipping git metadata, ignored paths, and dependency trees."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from pathspec.gitignore import GitIgnoreSpec

from repotree.buildfiles import classify_file

# Dependency trees. --no-ignore disables this list. .git is always omitted.
_SKIP_DIR_NAMES = frozenset(
    {
        "node_modules",
        "vendor",
        "venv",
        ".venv",
        "dist",
        "target",
        "__pycache__",
    }
)


@dataclass
class RawDir:
    """Files and child directories directly inside one walked directory."""

    entries: list[tuple[str, str, str]] = field(default_factory=list)
    children: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def collect(root: Path, *, ignore: bool = True) -> dict[str, RawDir]:
    """Return directories keyed by relative POSIX path, ``""`` for the root.

    ``.git`` and gitignored paths are omitted. Dependency directories are
    recorded on their parent as ``skipped`` and are not entered. Symlinks
    are not followed.
    """
    resolved = Path(root).resolve()
    if not resolved.is_dir():
        raise NotADirectoryError(resolved)
    spec = _gitignore(resolved) if ignore else None
    found: dict[str, RawDir] = {}
    for dirpath, dirnames, filenames in os.walk(resolved):
        current = Path(dirpath)
        relative = "" if current == resolved else current.relative_to(resolved).as_posix()
        raw = found.setdefault(relative, RawDir())
        enter: list[str] = []
        skipped: list[str] = []
        for name in dirnames:
            fate = _fate(resolved, current, name, spec, ignore=ignore)
            if fate == "enter":
                enter.append(name)
            elif fate == "skip":
                skipped.append(name)
        dirnames[:] = enter
        raw.children = enter
        raw.skipped = skipped
        for name in filenames:
            path = current / name
            if not path.is_file() or path.is_symlink():
                continue
            file_relative = name if not relative else f"{relative}/{name}"
            if spec is not None and spec.match_file(file_relative):
                continue
            kind, label = classify_file(path, file_relative)
            raw.entries.append((name, kind, label))
    found.setdefault("", RawDir())
    return found


def _gitignore(root: Path) -> GitIgnoreSpec | None:
    path = root / ".gitignore"
    if not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return GitIgnoreSpec.from_lines(lines)


def _fate(
    root: Path,
    current: Path,
    name: str,
    spec: GitIgnoreSpec | None,
    *,
    ignore: bool,
) -> str:
    """``enter``, ``skip`` (record, do not descend), or ``omit``."""
    path = current / name
    if path.is_symlink() or name == ".git":
        return "omit"
    if not ignore:
        return "enter"
    if name in _SKIP_DIR_NAMES:
        return "skip"
    if spec is None:
        return "enter"
    relative = path.relative_to(root).as_posix()
    if spec.match_file(relative) or spec.match_file(relative + "/"):
        return "omit"
    return "enter"
