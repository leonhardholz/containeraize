"""Collapse directories that contain no package manifest."""

from __future__ import annotations

from collections import Counter

from repotree.report import Collapsed, Node
from repotree.walk import RawDir


def summarize(dirs: dict[str, RawDir]) -> Node:
    """Build the expanded spine and roll every other subtree into one line."""
    expanded = _expanded(dirs)
    return _node("", dirs, expanded)


def _expanded(dirs: dict[str, RawDir]) -> set[str]:
    """Root, every manifest directory, and every ancestor of one."""
    found = {""}
    for relative, raw in dirs.items():
        if not any(kind == "marker" for _name, kind, _label in raw.entries):
            continue
        found.add(relative)
        parent = relative
        while parent:
            parent = parent.rpartition("/")[0]
            found.add(parent)
    return found


def _node(relative: str, dirs: dict[str, RawDir], expanded: set[str]) -> Node:
    raw = dirs.get(relative, RawDir())
    markers: list[str] = []
    build: list[str] = []
    counts: Counter[str] = Counter()
    for name, kind, label in raw.entries:
        if kind == "marker":
            markers.append(name)
        elif kind == "build":
            build.append(name)
        else:
            counts[label] += 1
    collapsed: list[Collapsed] = []
    children: list[Node] = []
    for name in raw.children:
        child = _join(relative, name)
        if child in expanded:
            children.append(_node(child, dirs, expanded))
        else:
            collapsed.append(_rollup(child, name, dirs))
    return Node(
        path=relative,
        name=relative.rpartition("/")[2],
        markers=tuple(sorted(markers)),
        build=tuple(sorted(build)),
        files=_sorted_counts(counts),
        file_count=sum(counts.values()),
        skipped=tuple(sorted(raw.skipped)),
        collapsed=tuple(sorted(collapsed, key=lambda item: item.name)),
        children=tuple(sorted(children, key=lambda item: item.name)),
    )


def _rollup(relative: str, name: str, dirs: dict[str, RawDir]) -> Collapsed:
    raw = dirs.get(relative, RawDir())
    counts: Counter[str] = Counter()
    build: list[str] = []
    file_count = 0
    for entry, kind, label in raw.entries:
        if kind == "marker":
            continue
        if kind == "build":
            build.append(entry)
        else:
            file_count += 1
            counts[label] += 1
    directories = 0
    depth = 0
    for child_name in raw.children:
        directories += 1
        inner = _rollup(_join(relative, child_name), child_name, dirs)
        file_count += inner.file_count
        counts.update(dict(inner.files))
        for item in inner.build:
            build.append(f"{child_name}/{item}")
        directories += inner.directories
        depth = max(depth, inner.depth + 1)
    for _skipped in raw.skipped:
        directories += 1
        depth = max(depth, 1)
    return Collapsed(
        path=relative,
        name=name,
        build=tuple(sorted(build)),
        files=_sorted_counts(counts),
        file_count=file_count,
        directories=directories,
        depth=depth,
    )


def _sorted_counts(counts: Counter[str]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _join(parent: str, name: str) -> str:
    return f"{parent}/{name}" if parent else name
