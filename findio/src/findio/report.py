"""Report types returned by scan()."""

from __future__ import annotations

import json
from dataclasses import dataclass

_KIND_ORDER = ("filesystem", "network", "service", "process", "database")


@dataclass(frozen=True)
class Symbol:
    language: str
    module: str
    symbol: str


@dataclass(frozen=True)
class Label:
    io: bool
    kinds: tuple[str, ...]


@dataclass(frozen=True)
class Location:
    path: str
    lines: tuple[int, ...]


@dataclass(frozen=True)
class SymbolGroup:
    language: str
    module: str
    symbol: str
    kinds: tuple[str, ...]
    locations: tuple[Location, ...]

    def qualified(self) -> str:
        if self.module and self.symbol:
            return f"{self.module}.{self.symbol}"
        return self.symbol or self.module


@dataclass(frozen=True)
class Report:
    version: int
    root: str
    symbols: tuple[SymbolGroup, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "root": self.root,
            "files": [
                {
                    "path": path,
                    "symbols": [
                        {
                            "language": group.language,
                            "module": group.module,
                            "symbol": group.symbol,
                            "kinds": list(group.kinds),
                            "lines": list(numbers),
                        }
                        for group, numbers in entries
                    ],
                }
                for path, entries in self._by_file()
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2) + "\n"

    def to_text(self) -> str:
        blocks: list[str] = []
        for path, entries in self._by_file():
            lines = [path]
            for group, numbers in entries:
                kinds = ",".join(group.kinds)
                listed = ",".join(str(number) for number in numbers)
                lines.append(f"  {kinds}  {group.language}  {group.qualified()}  {listed}")
            blocks.append("\n".join(lines))
        if not blocks:
            return ""
        return "\n\n".join(blocks) + "\n"

    def _by_file(self) -> list[tuple[str, list[tuple[SymbolGroup, tuple[int, ...]]]]]:
        by_path: dict[str, list[tuple[SymbolGroup, tuple[int, ...]]]] = {}
        for group in self.symbols:
            for location in group.locations:
                by_path.setdefault(location.path, []).append((group, location.lines))
        return [
            (path, sorted(by_path[path], key=lambda item: symbol_sort_key(item[0])))
            for path in sorted(by_path)
        ]


def symbol_sort_key(group: SymbolGroup) -> tuple[tuple[int, ...], str, str, str]:
    kind_rank = tuple(
        _KIND_ORDER.index(kind) if kind in _KIND_ORDER else len(_KIND_ORDER) for kind in group.kinds
    )
    return (kind_rank, group.language, group.module, group.symbol)
