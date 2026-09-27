"""Summarize the directory structure of one or more repositories."""

from __future__ import annotations

import sys
from pathlib import Path

from repotree.report import Report, Root
from repotree.summary import summarize
from repotree.walk import collect

__all__ = ["Report", "scan"]


def scan(*roots: Path, ignore: bool = True) -> Report:
    """Walk each root and return one structural abstract.

    The root is always expanded. A directory is expanded when it contains a
    package manifest or is an ancestor of one, and then every subdirectory is
    listed. Any other directory is one summary line. Manifests and build files
    are named. Other files are counts by language, or ``other``.
    """
    if not roots:
        raise ValueError("at least one repository root")
    resolved = _unique(roots)
    _log("walk")
    names = _names(resolved)
    scanned: list[Root] = []
    seen = 0
    for root in resolved:
        dirs = collect(root, ignore=ignore)
        seen += sum(len(raw.entries) for raw in dirs.values())
        scanned.append(Root(str(root), names[root], summarize(dirs)))
    _log(f"walk {seen}")
    return Report(version=1, roots=tuple(scanned))


def _unique(roots: tuple[Path, ...]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        resolved = Path(root).resolve()
        if resolved not in found:
            found.append(resolved)
    return found


def _names(roots: list[Path]) -> dict[Path, str]:
    """Empty when one root is scanned. Otherwise the directory name, with ``-2`` on a clash."""
    if len(roots) < 2:
        return {root: "" for root in roots}
    used: dict[str, int] = {}
    names: dict[Path, str] = {}
    for root in roots:
        seen = used.get(root.name, 0)
        used[root.name] = seen + 1
        names[root] = root.name if seen == 0 else f"{root.name}-{seen + 1}"
    return names


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)
