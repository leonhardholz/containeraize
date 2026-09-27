"""Language detection on a temporary tree."""

from __future__ import annotations

from pathlib import Path

from findio import scan
from findio.detect import detect


def test_detect_keeps_parsing_sources_and_shell_shebangs(tmp_path: Path, capsys) -> None:
    (tmp_path / "keep.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "run.sh").write_text("echo hi\n", encoding="utf-8")
    (tmp_path / "tool").write_text("#!/usr/bin/env dash\necho hi\n", encoding="utf-8")
    (tmp_path / "ksh-tool").write_text("#!/bin/ksh\necho hi\n", encoding="utf-8")
    (tmp_path / "globs.zsh").write_text("ls *(.)\n", encoding="utf-8")
    (tmp_path / "init.fish").write_text("set -l x 1\n", encoding="utf-8")
    (tmp_path / "data.json").write_text('{"a": 1}\n', encoding="utf-8")
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("this is not python at all !!!\n", encoding="utf-8")

    assert detect(tmp_path / "keep.py") == "python"
    assert detect(tmp_path / "run.sh") == "shell"
    assert detect(tmp_path / "tool") == "shell"
    assert detect(tmp_path / "ksh-tool") == "shell"
    assert detect(tmp_path / "globs.zsh") == "shell"
    assert detect(tmp_path / "init.fish") == "shell"
    assert detect(tmp_path / "data.json") == "json"
    (tmp_path / "plain.h").write_text(
        "#ifndef PLAIN_H\n#define PLAIN_H\nint add(int a, int b);\n#endif\n",
        encoding="utf-8",
    )
    (tmp_path / "gen.h").write_text(
        "#pragma once\n"
        "namespace Sample {\n"
        "class CodeGenerator {\n"
        "public:\n"
        "    virtual void run() = 0;\n"
        "    template<typename T>\n"
        "    void accept(const T& value);\n"
        "};\n"
        "}\n",
        encoding="utf-8",
    )
    assert detect(tmp_path / "plain.h") == "c"
    assert detect(tmp_path / "gen.h") == "cpp"
    fields = "\n".join(f"int field_{index};" for index in range(40))
    (tmp_path / "soft.h").write_text(
        "struct Box {\n" + fields + "\n};\n"
        'static const std::string REMOTING__NAMESPACE("http://example.com/"s);\n'
        "void serialize();\n",
        encoding="utf-8",
    )
    assert detect(tmp_path / "soft.h") == "cpp"
    assert detect(tmp_path / "notes.txt") is None
    assert detect(tmp_path / "bad.py") is None

    (tmp_path / "wide.py").write_bytes("print('ok')\n".encode("utf-16"))
    (tmp_path / "latin.py").write_bytes("# coding: latin-1\n# café\nprint('ok')\n".encode("latin-1"))
    (tmp_path / "euro.py").write_bytes("# coding: cp1252\n# €\nprint('ok')\n".encode("cp1252"))
    (tmp_path / "zsh-utf16").write_bytes("#!/bin/zsh\necho hi\n".encode("utf-16"))
    assert detect(tmp_path / "wide.py") == "python"
    assert detect(tmp_path / "latin.py") == "python"
    assert detect(tmp_path / "euro.py") == "python"
    assert detect(tmp_path / "zsh-utf16") == "shell"

    report = scan(tmp_path, client=None, cache=None)
    printed = set(capsys.readouterr().err.splitlines())
    phases = {line for line in printed if line.split(" ", 1)[0] in {"walk", "parse", "filter", "classify", "report"}}
    printed -= phases
    assert {line.split(" ", 1)[0] for line in phases} == {"walk", "parse", "filter", "classify", "report"}

    assert [(item.symbol, item.locations[0].path) for item in report.symbols] == [("ls", "globs.zsh")]
    assert printed == {
        "keep.py python",
        "run.sh shell",
        "tool shell",
        "ksh-tool shell",
        "globs.zsh shell",
        "plain.h c",
        "gen.h cpp",
        "soft.h cpp",
        "init.fish shell",
        "data.json json",
        "wide.py python",
        "latin.py python",
        "euro.py python",
        "zsh-utf16 shell",
    }
