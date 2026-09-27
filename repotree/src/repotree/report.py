"""Report types returned by scan()."""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Collapsed:
    """A directory with no manifest in its subtree, rolled into one summary."""

    path: str
    name: str
    build: tuple[str, ...]
    files: tuple[tuple[str, int], ...]
    file_count: int
    directories: int
    depth: int


@dataclass(frozen=True)
class Node:
    """An expanded directory: the root, a manifest directory, or an ancestor of one."""

    path: str
    name: str
    markers: tuple[str, ...]
    build: tuple[str, ...]
    files: tuple[tuple[str, int], ...]
    file_count: int
    skipped: tuple[str, ...]
    collapsed: tuple[Collapsed, ...]
    children: tuple[Node, ...]


@dataclass(frozen=True)
class Root:
    path: str
    name: str
    tree: Node


@dataclass(frozen=True)
class Report:
    version: int
    roots: tuple[Root, ...]

    def to_dict(self) -> dict[str, object]:
        return {"version": self.version, "roots": [_root_dict(root) for root in self.roots]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2) + "\n"

    def to_text(self) -> str:
        multi = len(self.roots) > 1
        blocks: list[str] = []
        for root in self.roots:
            body = "\n".join(_render_node(root.tree, ""))
            if multi:
                blocks.append(f"{root.path}\n{body}" if body else root.path)
            else:
                blocks.append(body)
        text = "\n\n".join(blocks)
        return text + "\n" if text else ""


def _root_dict(root: Root) -> dict[str, object]:
    return {"path": root.path, "name": root.name, "tree": _node_dict(root.tree)}


def _node_dict(node: Node) -> dict[str, object]:
    return {
        "path": node.path,
        "markers": list(node.markers),
        "build": list(node.build),
        "files": dict(node.files),
        "file_count": node.file_count,
        "skipped": list(node.skipped),
        "collapsed": [_collapsed_dict(item) for item in node.collapsed],
        "children": [_node_dict(child) for child in node.children],
    }


def _collapsed_dict(item: Collapsed) -> dict[str, object]:
    return {
        "path": item.path,
        "build": list(item.build),
        "files": dict(item.files),
        "file_count": item.file_count,
        "directories": item.directories,
        "depth": item.depth,
    }


def _render_node(node: Node, indent: str) -> list[str]:
    lines: list[str] = []
    for name in node.markers:
        lines.append(f"{indent}{name}")
    for name in node.build:
        lines.append(f"{indent}{name}")
    counts = _format_counts(node.files)
    if counts:
        lines.append(f"{indent}{counts}")
    for name in node.skipped:
        lines.append(f"{indent}{name}/  skipped")
    folded = {item.name: item for item in node.collapsed}
    opened = {child.name: child for child in node.children}
    for name in sorted(folded.keys() | opened.keys()):
        if name in opened:
            lines.append(f"{indent}{name}/")
            lines.extend(_render_node(opened[name], indent + "  "))
        else:
            lines.append(f"{indent}{_collapsed_line(folded[name])}")
    return lines


def _collapsed_line(item: Collapsed) -> str:
    parts = [f"{item.name}/"]
    if item.build:
        parts.append("  ".join(item.build))
    if item.file_count:
        noun = "file" if item.file_count == 1 else "files"
        parts.append(f"{item.file_count} {noun}")
        counts = _format_counts(item.files)
        if counts:
            parts.append(counts)
    if item.directories:
        noun = "dir" if item.directories == 1 else "dirs"
        parts.append(f"{item.directories} {noun}")
        parts.append(f"depth {item.depth}")
    return "  ".join(parts)


def _format_counts(files: tuple[tuple[str, int], ...]) -> str:
    return "  ".join(f"{name} ×{count}" for name, count in files)
