"""Package manifest names and the findio language suffix map."""

from __future__ import annotations

# Filenames from the Languages section of findio's README. A directory that
# contains one of these is a package boundary. Lockfiles are not markers.
_EXACT = frozenset(
    {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "Pipfile",
        "package.json",
        "pnpm-workspace.yaml",
        "go.mod",
        "go.work",
        "Cargo.toml",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "Gemfile",
        "composer.json",
        "CMakeLists.txt",
        "meson.build",
    }
)

_SUFFIXES = (".csproj", ".sln", ".slnx", ".gemspec")

# A .psd1 is a module manifest only when the probe finds one of these keys.
_MODULE_KEYS = ("ModuleVersion", "RootModule", "ModuleToProcess")

# Suffixes for the same languages. Anything else, including no suffix, is other.
_LANGUAGES = {
    ".py": "Python",
    ".pyi": "Python",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".mts": "TypeScript",
    ".cts": "TypeScript",
    ".tsx": "TSX",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
    ".hh": "C++",
    ".hxx": "C++",
    ".ps1": "PowerShell",
    ".psm1": "PowerShell",
    ".psd1": "PowerShell",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".fish": "shell",
    ".ksh": "shell",
}


def is_marker(name: str) -> bool:
    """True when this filename is a package manifest.

    A ``.psd1`` file is not decided here. ``classify_file`` reads it and keeps
    it only when :func:`is_module_manifest` matches.
    """
    if name in _EXACT:
        return True
    lower = name.lower()
    return lower.endswith(_SUFFIXES)


def is_module_manifest(text: str) -> bool:
    """True when ``text`` contains a PowerShell module-manifest key."""
    return any(key in text for key in _MODULE_KEYS)


def language_of(name: str) -> str:
    """Language label for a file, or ``other`` when the suffix is not supported."""
    lower = name.lower()
    dot = lower.rfind(".")
    if dot <= 0:
        return "other"
    return _LANGUAGES.get(lower[dot:], "other")

