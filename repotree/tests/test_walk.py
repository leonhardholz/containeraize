"""Skip rules for the repository walk."""

from pathlib import Path

from repotree import scan


def test_skip_list_and_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("secret.py\nhidden/\n", encoding="utf-8")
    (tmp_path / "secret.py").write_text("x = 1\n", encoding="utf-8")
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    (hidden / "note.md").write_text("secret\n", encoding="utf-8")
    (tmp_path / "keep.py").write_text("print('ok')\n", encoding="utf-8")
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    modules = tmp_path / "node_modules"
    modules.mkdir()
    (modules / "index.js").write_text("module.exports = {}\n", encoding="utf-8")
    output = tmp_path / "build"
    output.mkdir()
    (output / "out.o").write_text("object\n", encoding="utf-8")
    (output / "package.json").write_text("{}\n", encoding="utf-8")
    source = tmp_path / "pkg" / "build"
    source.mkdir(parents=True)
    (source / "lib.go").write_text("package build\n", encoding="utf-8")
    link = tmp_path / "link.py"
    link.symlink_to(tmp_path / "keep.py")

    text = scan(tmp_path).to_text()

    assert "secret.py" not in text
    assert "note.md" not in text
    assert "hidden/" not in text
    assert "HEAD" not in text
    assert "link.py" not in text
    assert "node_modules/  skipped" in text
    assert "index.js" not in text
    assert "build/  skipped" not in text
    assert "package.json" in text
    assert "pkg/" in text
    assert "Go ×1" in text
    assert "Python ×1" in text


def test_no_ignore_walks_dependency_directories(tmp_path: Path) -> None:
    modules = tmp_path / "node_modules"
    modules.mkdir()
    (modules / "index.js").write_text("module.exports = {}\n", encoding="utf-8")

    text = scan(tmp_path, ignore=False).to_text()

    assert "skipped" not in text
    assert "JavaScript ×1" in text
