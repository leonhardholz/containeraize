"""The structural abstract for one repository and for several."""

import json
from pathlib import Path

from repotree import scan
from repotree.cli import main


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fixture(root: Path) -> None:
    _write(root, "package.json", "{}\n")
    _write(root, "README.md", "hello\n")
    _write(root, ".gitignore", "*.log\n")
    _write(root, "debug.log", "noise\n")
    _write(root, "Dockerfile", "FROM scratch\n")
    _write(root, "Makefile", "all:\n\ttrue\n")
    _write(root, "node_modules/index.js", "module.exports = {}\n")
    _write(root, "src/index.ts", "export {}\n")
    _write(root, "src/components/Button.tsx", "export {}\n")
    _write(root, "src/util/format.ts", "export {}\n")
    _write(root, "docs/guide.md", "guide\n")
    _write(root, "docs/images/logo.png", "png\n")
    _write(root, "packages/web/package.json", "{}\n")
    _write(root, "packages/web/src/main.ts", "export {}\n")
    _write(root, "packages/web/src/app.tsx", "export {}\n")
    _write(root, "packages/api/pom.xml", "<project/>\n")
    _write(root, "packages/api/src/main/java/App.java", "class App {}\n")
    _write(root, "packages/notes/todo.md", "todo\n")
    _write(root, "infra/web.yaml", "Resources:\n  Bucket:\n    Type: AWS::S3::Bucket\n")
    _write(root, "infra/app.json", '{"AWSTemplateFormatVersion": "2010-09-09"}\n')
    _write(root, "infra/notes.md", "notes\n")
    _write(root, "ops/ansible.cfg", "[defaults]\n")
    _write(root, "ops/webservers.yml", "- hosts: all\n  tasks:\n    - ansible.builtin.ping:\n")
    _write(root, "images/api", "FROM alpine:3.20\nRUN echo hi\n")
    _write(root, ".github/workflows/deploy.yml", "on:\n  push:\njobs:\n  deploy:\n    runs-on: ubuntu-latest\n")


_EXPECTED = """\
package.json
Dockerfile
Makefile
other ×2
node_modules/  skipped
.github/  workflows/deploy.yml  1 dir  depth 1
docs/  2 files  other ×2  1 dir  depth 1
images/  api
infra/  app.json  web.yaml  1 file  other ×1
ops/  ansible.cfg  webservers.yml
packages/
  api/
    pom.xml
    src/  1 file  Java ×1  2 dirs  depth 2
  notes/  1 file  other ×1
  web/
    package.json
    src/  2 files  TSX ×1  TypeScript ×1
src/  3 files  TypeScript ×2  TSX ×1  2 dirs  depth 1
"""


def test_abstract_names_markers_and_collapses_the_rest(tmp_path: Path, capsys) -> None:
    _fixture(tmp_path)
    report = scan(tmp_path)
    captured = capsys.readouterr()

    assert report.to_text() == _EXPECTED
    assert "components/" not in report.to_text()
    assert "debug.log" not in report.to_text()
    assert captured.err == f"walk\nwalk {23}\n"
    assert report.version == 1
    assert report.roots[0].name == ""
    body = json.loads(report.to_json())
    assert body["version"] == 1
    assert body["roots"][0]["tree"]["markers"] == ["package.json"]
    collapsed = {item["path"]: item for item in body["roots"][0]["tree"]["collapsed"]}
    assert collapsed["src"]["file_count"] == 3
    assert collapsed["src"]["files"] == {"TypeScript": 2, "TSX": 1}
    assert "Button.tsx" not in report.to_json()


def test_several_roots_keep_paths_relative(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "same" / "left"
    _write(left, "README.md", "left\n")
    _write(right, "main.py", "print('ok')\n")

    report = scan(left, right)
    text = report.to_text()

    assert text.startswith(f"{left.resolve()}\n")
    assert f"\n\n{right.resolve()}\n" in text
    assert "Python ×1" in text
    assert report.roots[0].name == "left"
    assert report.roots[1].name == "left-2"
    assert "left/README.md" not in text


def test_build_file_does_not_expand_a_directory(tmp_path: Path) -> None:
    _write(tmp_path, "deploy/web.yaml", "Resources:\n  Bucket:\n    Type: AWS::S3::Bucket\n")
    _write(tmp_path, "deploy/extra/readme.md", "hi\n")

    text = scan(tmp_path).to_text()

    assert text == "deploy/  web.yaml  1 file  other ×1  1 dir  depth 1\n"
    assert "extra/" not in text


def test_cli_writes_json(tmp_path: Path, capsys) -> None:
    _write(tmp_path, "pyproject.toml", "[project]\nname='demo'\n")
    output = tmp_path / "out.json"
    code = main([str(tmp_path), "--format", "json", "--output", str(output)])
    assert code == 0
    assert capsys.readouterr().out == ""
    body = json.loads(output.read_text(encoding="utf-8"))
    assert body["roots"][0]["tree"]["markers"] == ["pyproject.toml"]
