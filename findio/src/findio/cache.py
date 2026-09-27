"""SQLite cache of one classification per language and symbol."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from findio.report import Label, Symbol


def default_cache_path() -> Path:
    """$XDG_CACHE_HOME/findio/classifications.sqlite, or ~/.cache."""
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "findio" / "classifications.sqlite"


def cache_key(symbol: Symbol, *, model: str, prompt_version: str) -> str:
    raw = "\0".join((prompt_version, model, symbol.language, symbol.module, symbol.symbol))
    return hashlib.sha256(raw.encode()).hexdigest()


class ClassificationCache:
    """One row per symbol. The batch that produced the row is not stored."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS classifications (
                cache_key      TEXT PRIMARY KEY,
                language       TEXT NOT NULL,
                module         TEXT NOT NULL,
                symbol         TEXT NOT NULL,
                model          TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                io             INTEGER NOT NULL,
                kinds          TEXT NOT NULL,
                created_at     TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def get(self, symbol: Symbol, *, model: str, prompt_version: str) -> Label | None:
        row = self._connection.execute(
            "SELECT io, kinds FROM classifications WHERE cache_key = ?",
            (cache_key(symbol, model=model, prompt_version=prompt_version),),
        ).fetchone()
        if row is None:
            return None
        io, kinds = row
        return Label(bool(io), tuple(json.loads(kinds)))

    def put(self, symbol: Symbol, label: Label, *, model: str, prompt_version: str) -> None:
        self._connection.execute(
            """
            INSERT OR REPLACE INTO classifications (
                cache_key, language, module, symbol, model, prompt_version, io, kinds, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cache_key(symbol, model=model, prompt_version=prompt_version),
                symbol.language,
                symbol.module,
                symbol.symbol,
                model,
                prompt_version,
                int(label.io),
                json.dumps(list(label.kinds)),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._connection.commit()
