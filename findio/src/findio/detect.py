"""Choose a tree-sitter grammar for a file and confirm it parses."""

from __future__ import annotations

from pathlib import Path

from tree_sitter import Parser
from tree_sitter_language_pack import (
    detect_language,
    detect_language_from_content,
    get_parser,
    has_language,
)

from findio.text import decode_source

# Skip a candidate when more than this fraction of syntax nodes are errors.
_ERROR_SHARE_LIMIT = 0.05

# Dialects reported as one language, "shell". The parse still uses the
# dialect grammar when the pack has one, so zsh syntax is not judged as bash.
_SHELL_DIALECTS = frozenset(
    {"bash", "csh", "dash", "fish", "ksh", "sh", "shell", "tcsh", "zsh"}
)
_SHELL_PARSE_FALLBACK = "bash"

_parsers: dict[str, Parser] = {}


def detect(path: Path) -> str | None:
    """Return the language name for ``path``, or None.

    The grammar comes from the file path, or from the shebang when the path
    has none. Bash, zsh, fish, ksh, dash, and the other Unix shells are
    reported as ``shell``. A candidate is kept only when a parse leaves at
    most ``_ERROR_SHARE_LIMIT`` of the syntax nodes in error. The language
    pack does not expose alternate grammars, except that a ``.h`` file is C++
    when that grammar has a strictly lower error share than C.
    """
    text = decode_source(path.read_bytes())
    source = text.encode("utf-8")
    name = detect_language(str(path))
    if name is None:
        name = detect_language_from_content(text)
    if name is None:
        name = _shebang_interpreter(text)
    grammar = _grammar(name)
    if grammar is None:
        return None
    share = _error_share(grammar, source)
    if path.suffix.lower() == ".h" and grammar == "c" and has_language("cpp"):
        cpp_share = _error_share("cpp", source)
        if cpp_share < share:
            name = "cpp"
            share = cpp_share
    if share > _ERROR_SHARE_LIMIT:
        return None
    if name in _SHELL_DIALECTS:
        return "shell"
    return name


def _grammar(name: str | None) -> str | None:
    if name is None:
        return None
    if name in _SHELL_DIALECTS:
        if has_language(name):
            return name
        if has_language(_SHELL_PARSE_FALLBACK):
            return _SHELL_PARSE_FALLBACK
        return None
    if has_language(name):
        return name
    return None


def _shebang_interpreter(text: str) -> str | None:
    """Interpreter name from the first line, when it is a Unix shell."""
    line = text.split("\n", 1)[0].strip()
    if not line.startswith("#!"):
        return None
    parts = line[2:].strip().split()
    if not parts:
        return None
    program = Path(parts[0]).name
    if program == "env":
        args = [part for part in parts[1:] if not part.startswith("-")]
        if not args:
            return None
        program = Path(args[0]).name
    if program in _SHELL_DIALECTS:
        return program
    return None


def _error_share(language: str, source: bytes) -> float:
    tree = _parser(language).parse(source)
    total = 0
    errors = 0
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        total += 1
        if node.is_error or node.is_missing:
            errors += 1
        stack.extend(node.child(index) for index in range(node.child_count))
    if total == 0:
        return 1.0
    return errors / total


def _parser(language: str) -> Parser:
    parser = _parsers.get(language)
    if parser is None:
        parser = get_parser(language)
        _parsers[language] = parser
    return parser
