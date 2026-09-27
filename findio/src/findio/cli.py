"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from findio import scan
from findio.ai import AIClassifier, litellm_usable
from findio.cache import ClassificationCache, default_cache_path
from findio.classify import CascadingClassifier


def available_cores() -> int:
    """Logical CPUs this process is allowed to run on."""
    count = os.process_cpu_count() if hasattr(os, "process_cpu_count") else os.cpu_count()
    return count or 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="findio")
    parser.add_argument("path", nargs="+", type=Path, help="repository roots to scan")
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="report format (default: text)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write the report here instead of stdout",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=available_cores(),
        help="worker processes (default: available cores)",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        help="SQLite classification cache (default: $XDG_CACHE_HOME/findio/classifications.sqlite)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="ask the model again and overwrite cached labels",
    )
    parser.add_argument(
        "--no-ignore",
        action="store_true",
        help="do not apply .gitignore or the dependency/build directory skip list",
    )
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    report = scan(
        *args.path,
        client=_client(args.cache, args.refresh),
        ignore=not args.no_ignore,
        jobs=args.jobs,
    )
    text = report.to_json() if args.format == "json" else report.to_text()
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text, encoding="utf-8")
    return 0


def _client(cache_path: Path | None, refresh: bool) -> CascadingClassifier | None:
    """The cascade when LiteLLM is configured, otherwise the static default."""
    setup = litellm_usable()
    if setup is None:
        return None
    model, api_base = setup
    cache = ClassificationCache(cache_path or default_cache_path())
    return CascadingClassifier(cache, AIClassifier(model, api_base), refresh=refresh)
