"""Manifest names and language labels."""

from pathlib import Path

from repotree.buildfiles import classify_file
from repotree.markers import is_marker, is_module_manifest, language_of


def test_markers_match_findio_languages() -> None:
    assert is_marker("package.json")
    assert is_marker("pnpm-workspace.yaml")
    assert is_marker("pom.xml")
    assert is_marker("build.gradle.kts")
    assert is_marker("pyproject.toml")
    assert is_marker("go.mod")
    assert is_marker("Cargo.toml")
    assert is_marker("Gemfile")
    assert is_marker("composer.json")
    assert is_marker("CMakeLists.txt")
    assert is_marker("app.csproj")
    assert is_marker("App.sln")
    assert is_marker("RestSharp.slnx")
    assert is_marker("widget.gemspec")
    assert not is_marker("Module.psd1")
    assert not is_marker("package-lock.json")
    assert not is_marker("requirements.txt")
    assert not is_marker("Dockerfile")


def test_psd1_is_a_manifest_only_with_a_module_key(tmp_path: Path) -> None:
    manifest = "@{\n    ModuleVersion = '1.0.0'\n    RootModule = 'Mod.psm1'\n}\n"
    strings = "@{ Confirm = 'Yes' }\n"
    path = tmp_path / "Mod.psd1"
    path.write_text(manifest, encoding="utf-8")
    assert is_module_manifest(manifest)
    assert classify_file(path, "Mod.psd1") == ("marker", "")
    data = tmp_path / "Mod.strings.psd1"
    data.write_text(strings, encoding="utf-8")
    assert classify_file(data, "Mod.strings.psd1") == ("file", "PowerShell")


def test_language_labels_and_other() -> None:
    assert language_of("Button.test.tsx") == "TSX"
    assert language_of("lib.d.ts") == "TypeScript"
    assert language_of("main.h") == "C"
    assert language_of("main.hpp") == "C++"
    assert language_of("run.PS1") == "PowerShell"
    assert language_of("Dockerfile") == "other"
    assert language_of(".gitignore") == "other"
    assert language_of("README.md") == "other"
    assert language_of("notes.tar.gz") == "other"
