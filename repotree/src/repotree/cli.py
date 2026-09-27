"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from repotree import scan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="repotree")
    parser.add_argument("path", nargs="+", type=Path, help="repository roots to summarize")
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="report format (default: text)",
    )
    parser.add_argument("--output", type=Path, help="write the report here instead of stdout")
    parser.add_argument(
        "--no-ignore",
        action="store_true",
        help="do not apply .gitignore or the dependency directory skip list",
    )
    args = parser.parse_args(argv)
    report = scan(*args.path, ignore=not args.no_ignore)
    text = report.to_json() if args.format == "json" else report.to_text()
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text, encoding="utf-8")
    return 0
