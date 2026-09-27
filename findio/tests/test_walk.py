"""Walker skip rules, using a temporary tree built in the test."""

from __future__ import annotations

import os
from pathlib import Path

from findio.walk import walk


def test_walk_skips_ignored_vendored_large_and_binary_files(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("ignored.txt\nskipped_dir/\n", encoding="utf-8")
    (tmp_path / "keep.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("secret\n", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "ok.txt").write_text("ok\n", encoding="utf-8")
    go_pkg = tmp_path / "nested" / "build"
    go_pkg.mkdir()
    (go_pkg / "lib.go").write_text("package build\nfunc NewOrchestrator() {}\n", encoding="utf-8")
    py_pkg = tmp_path / "pkg" / "build"
    py_pkg.mkdir(parents=True)
    (py_pkg / "main.py").write_text("print('ok')\n", encoding="utf-8")
    data_pkg = tmp_path / "data" / "build"
    data_pkg.mkdir(parents=True)
    (data_pkg / "compile_commands.json").write_text("{}\n", encoding="utf-8")

    skipped_dir = tmp_path / "skipped_dir"
    skipped_dir.mkdir()
    (skipped_dir / "a.py").write_text("x = 1\n", encoding="utf-8")

    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    for directory, filename, contents in (
        ("node_modules", "pkg.js", "module.exports = {}\n"),
        ("vendor", "lib.go", "package lib\n"),
        ("venv", "lib.py", "x = 1\n"),
        (".venv", "lib.py", "x = 1\n"),
        ("dist", "out.js", "bundle\n"),
        ("build", "out.o", "object\n"),
        ("target", "app", "bin\n"),
        ("__pycache__", "x.pyc", "cache\n"),
    ):
        parent = tmp_path / directory
        parent.mkdir()
        (parent / filename).write_text(contents, encoding="utf-8")

    big = tmp_path / "big.bin"
    big.touch()
    os.truncate(big, 10 * 1024 * 1024 + 1)
    (tmp_path / "binary.dat").write_bytes(b"hello\0world")
    (tmp_path / "late_nul.dat").write_bytes(b"a" * 8192 + b"\0")
    (tmp_path / "wide.py").write_bytes("print('ok')\n".encode("utf-16"))

    visited = {relative.as_posix() for _root, relative in walk(tmp_path)}

    expected = {
        ".gitignore",
        "keep.py",
        "nested/ok.txt",
        "nested/build/lib.go",
        "pkg/build/main.py",
        "late_nul.dat",
        "wide.py",
    }
    skipped = {
        "ignored.txt",
        "skipped_dir/a.py",
        ".git/HEAD",
        "node_modules/pkg.js",
        "vendor/lib.go",
        "venv/lib.py",
        ".venv/lib.py",
        "dist/out.js",
        "build/out.o",
        "data/build/compile_commands.json",
        "target/app",
        "__pycache__/x.pyc",
        "big.bin",
        "binary.dat",
    }

    assert visited == expected
    assert visited.isdisjoint(skipped)
