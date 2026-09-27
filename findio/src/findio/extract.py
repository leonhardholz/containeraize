"""Find import and call symbols a static classifier can label."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_parser

from findio.text import decode_source
from findio.walk import _SKIP_DIR_NAMES

_WRAPPERS = frozenset(
    {"sudo", "command", "exec", "env", "nice", "nohup", "time", "xargs", "builtin"}
)
_SHELL_SOURCES = frozenset({".", "source"})
_REDIRECTS = frozenset({"file_redirect", "herestring", "heredoc", "heredoc_redirect"})
# Built-in PowerShell aliases for cmdlets the classifier knows. The reported
# symbol is the cmdlet, which is the name a later model can recognize.
_POWERSHELL_ALIASES = {
    "ac": "Add-Content",
    "cat": "Get-Content",
    "clc": "Clear-Content",
    "copy": "Copy-Item",
    "cp": "Copy-Item",
    "cpi": "Copy-Item",
    "curl": "Invoke-WebRequest",
    "del": "Remove-Item",
    "dir": "Get-ChildItem",
    "erase": "Remove-Item",
    "gc": "Get-Content",
    "gci": "Get-ChildItem",
    "icm": "Invoke-Command",
    "irm": "Invoke-RestMethod",
    "iwr": "Invoke-WebRequest",
    "ls": "Get-ChildItem",
    "mi": "Move-Item",
    "move": "Move-Item",
    "mv": "Move-Item",
    "ni": "New-Item",
    "rd": "Remove-Item",
    "ren": "Rename-Item",
    "ri": "Remove-Item",
    "rm": "Remove-Item",
    "rmdir": "Remove-Item",
    "rni": "Rename-Item",
    "saps": "Start-Process",
    "sc": "Set-Content",
    "start": "Start-Process",
    "type": "Get-Content",
    "wget": "Invoke-WebRequest",
}
_parsers: dict[str, Parser] = {}


@dataclass(frozen=True)
class Occurrence:
    module: str
    symbol: str
    line: int
    # True when the call is written `command name`, which skips a shell function.
    skips_function: bool = False
    # Enclosing class of an unqualified C++ call. The call stays a bare name
    # unless that class declares the method.
    owner: str = ""


@dataclass(frozen=True)
class Definitions:
    """Names this file contributes to the repository.

    ``functions`` are bare functions (C, C++, PHP, shell, PowerShell) and Ruby
    method names. ``scopes`` are declared types and members. A type is its
    qualified name. A member is ``module`` and ``symbol`` joined with a NUL.
    A package name by itself is not a scope. A Ruby member is the class and
    the method, and also each suffix of that class path.
    """

    functions: frozenset[str] = frozenset()
    scopes: frozenset[str] = frozenset()
    # Literal ``source`` and ``.`` paths in a shell file. A variable path is omitted.
    sources: frozenset[str] = frozenset()
    # Rust ``pub extern crate crate_name as alias`` in a crate root. Each pair
    # is the public path and the crate it names, such as ``("grep::cli", "grep_cli")``.
    aliases: frozenset[tuple[str, str]] = frozenset()


def extract(path: Path, language: str, root: Path) -> list[Occurrence]:
    """Return symbols in ``path`` that are worth classifying."""
    return read_file(path, language, root)[0]


def read_file(path: Path, language: str, root: Path) -> tuple[list[Occurrence], Definitions]:
    """Return symbols and the names this file defines for the rest of the repository.

    Function names cover C, C++, PHP, shell, PowerShell, and Ruby method names.
    A PowerShell DSC resource name from that module is included too.
    A static C or C++ function is omitted and is only hidden in the file that
    defines it. A C++ method is a member of its class, not a bare function.
    Java, Kotlin, Go, Rust, JavaScript, and Ruby contribute the types and
    members they declare, not the package name alone.
    """
    source = decode_source(path.read_bytes()).encode("utf-8")
    defined: set[str] = set()
    scopes: set[str] = set()
    sources: set[str] = set()
    aliases: set[tuple[str, str]] = set()
    if language == "python":
        found = _python(source, root)
    elif language in {"javascript", "typescript", "tsx"}:
        found, scopes = _javascript(source, language, path, root)
    elif language == "go":
        found, scopes = _go(source, path, root)
    elif language == "rust":
        found, scopes, aliases = _rust(source, path, root)
    elif language == "java":
        found, scopes = _java(source)
    elif language == "kotlin":
        found, scopes = _kotlin(source)
    elif language == "csharp":
        found, scopes = _csharp(source)
    elif language in {"c", "cpp"}:
        found, defined, scopes = _c_family(source, language, path, root)
    elif language == "php":
        found, defined = _php(source, path, root)
    elif language == "ruby":
        found, defined, scopes = _ruby(source, path, root)
    elif language == "powershell":
        found, defined = _powershell(source, path)
    elif language == "shell":
        found, defined, sources = _shell(source, path)
    else:
        return [], Definitions()
    return _drop_unused_modules(found), Definitions(
        frozenset(defined), frozenset(scopes), frozenset(sources), frozenset(aliases)
    )


def _python(source: bytes, root: Path) -> list[Occurrence]:
    tree = _parser("python").parse(source)
    bindings: dict[str, tuple[str, str | None]] = {}
    found: list[Occurrence] = []
    for node in _walk(tree.root_node):
        if node.type == "import_statement":
            for module, alias in _python_import(node, source):
                if _local(module, root):
                    continue
                # `import os.path` binds the name `os`. An alias binds the
                # whole dotted module: `import os.path as osp`.
                local = alias or module.split(".", 1)[0]
                bound = module if alias else local
                bindings[local] = (bound, None)
                found.append(Occurrence(module, "", _line(node)))
        elif node.type == "import_from_statement":
            imported = _python_from(node, source)
            if imported is None:
                continue
            module, names = imported
            if _local(module, root):
                continue
            for name, alias in names:
                bindings[alias or name] = (module, name)
                found.append(Occurrence(module, name, _line(node)))
        elif node.type == "call":
            found.extend(_python_call(node, source, bindings))
    return found


def _python_import(node: Node, source: bytes) -> list[tuple[str, str | None]]:
    modules: list[tuple[str, str | None]] = []
    for child in _children(node):
        if child.type == "dotted_name":
            modules.append((_text(child, source), None))
        elif child.type == "aliased_import":
            modules.append(_aliased(child, source))
    return modules


def _python_from(node: Node, source: bytes) -> tuple[str, list[tuple[str, str | None]]] | None:
    module = ""
    names: list[tuple[str, str | None]] = []
    for child in _children(node):
        if child.type == "relative_import":
            return None
        if child.type == "dotted_name" and not module:
            module = _text(child, source)
        elif child.type == "dotted_name":
            names.append((_text(child, source), None))
        elif child.type == "aliased_import":
            names.append(_aliased(child, source))
    if not module:
        return None
    return module, names


def _aliased(node: Node, source: bytes) -> tuple[str, str | None]:
    name = ""
    alias = None
    dotted = False
    for child in _children(node):
        if child.type == "dotted_name":
            name = _text(child, source)
            dotted = True
        elif child.type == "identifier" and dotted:
            alias = _text(child, source)
    return name, alias


def _python_call(
    node: Node,
    source: bytes,
    bindings: dict[str, tuple[str, str | None]],
) -> list[Occurrence]:
    function = next(
        (child for child in _children(node) if child.type in {"identifier", "attribute"}),
        None,
    )
    if function is None:
        return []
    if function.type == "identifier":
        name = _text(function, source)
        bound = bindings.get(name)
        if bound is not None:
            module, imported = bound
            return [Occurrence(module, imported or "", _line(node))]
        if name in {"open", "input"}:
            return [Occurrence("", name, _line(node))]
        return []
    chain = _attribute_chain(function, source)
    if chain is None:
        return []
    root_name, attrs = chain
    bound = bindings.get(root_name)
    if bound is None:
        return []
    module, imported = bound
    if imported:
        symbol = imported if not attrs else f"{imported}.{'.'.join(attrs)}"
    else:
        symbol = ".".join(attrs)
    return [Occurrence(module, symbol, _line(node))]


def _attribute_chain(node: Node, source: bytes) -> tuple[str, list[str]] | None:
    attrs: list[str] = []
    current = node
    while current.type == "attribute":
        parts = [child for child in _children(current) if child.type != "."]
        if len(parts) != 2:
            return None
        attrs.append(_text(parts[1], source))
        current = parts[0]
    if current.type != "identifier":
        return None
    attrs.reverse()
    return _text(current, source), attrs


def _javascript(source: bytes, language: str, path: Path, root: Path) -> tuple[list[Occurrence], set[str]]:
    tree = _parser(language).parse(source)
    bindings: dict[str, tuple[str, str | None]] = {}
    found: list[Occurrence] = []
    for node in _walk(tree.root_node):
        if node.type == "import_statement":
            found.extend(_js_import(node, source, root, bindings))
        elif node.type == "call_expression":
            found.extend(_js_call(node, source, bindings))
        elif node.type == "variable_declarator":
            _js_require(node, source, root, bindings, found)
    return found, _js_scopes(tree.root_node, source, path, root)


def _js_import(
    node: Node,
    source: bytes,
    root: Path,
    bindings: dict[str, tuple[str, str | None]],
) -> list[Occurrence]:
    module = _js_module(node, source)
    if not module or _js_internal(module, root):
        return []
    found = [Occurrence(module, "", _line(node))]
    for specifier in _walk(node):
        if specifier.type != "import_specifier":
            continue
        identifiers = [child for child in _children(specifier) if child.type == "identifier"]
        if not identifiers:
            continue
        name = _text(identifiers[0], source)
        alias = _text(identifiers[-1], source)
        bindings[alias] = (module, name)
        found.append(Occurrence(module, name, _line(node)))
    clause = next((child for child in _children(node) if child.type == "import_clause"), None)
    if clause is not None:
        default = next((child for child in _children(clause) if child.type == "identifier"), None)
        if default is not None:
            bindings[_text(default, source)] = (module, None)
        namespace = next((child for child in _children(clause) if child.type == "namespace_import"), None)
        if namespace is not None:
            local = next((child for child in _children(namespace) if child.type == "identifier"), None)
            if local is not None:
                bindings[_text(local, source)] = (module, None)
    return found


def _js_require(
    node: Node,
    source: bytes,
    root: Path,
    bindings: dict[str, tuple[str, str | None]],
    found: list[Occurrence],
) -> None:
    call = next((child for child in _children(node) if child.type == "call_expression"), None)
    if call is None:
        return
    function = next((child for child in _children(call) if child.type == "identifier"), None)
    if function is None or _text(function, source) != "require":
        return
    module = _js_module(call, source)
    if not module or _js_internal(module, root):
        return
    bound = False
    for child in _children(node):
        if child.type == "identifier":
            bindings[_text(child, source)] = (module, None)
            bound = True
        elif child.type == "object_pattern":
            _js_require_pattern(child, source, module, bindings)
            bound = True
    if bound:
        found.append(Occurrence(module, "", _line(node)))


def _js_require_pattern(
    node: Node,
    source: bytes,
    module: str,
    bindings: dict[str, tuple[str, str | None]],
) -> None:
    for child in _children(node):
        if child.type == "shorthand_property_identifier_pattern":
            name = _text(child, source)
            bindings[name] = (module, name)
        elif child.type == "pair_pattern":
            exported = next(
                (part for part in _children(child) if part.type == "property_identifier"),
                None,
            )
            local = next((part for part in _children(child) if part.type == "identifier"), None)
            if exported is not None and local is not None:
                bindings[_text(local, source)] = (module, _text(exported, source))


def _js_call(
    node: Node,
    source: bytes,
    bindings: dict[str, tuple[str, str | None]],
) -> list[Occurrence]:
    function = next(
        (
            child
            for child in _children(node)
            if child.type in {"identifier", "member_expression"}
        ),
        None,
    )
    if function is None:
        return []
    if function.type == "identifier":
        name = _text(function, source)
        if name in {"fetch", "XMLHttpRequest"}:
            return [Occurrence("", name, _line(node))]
        bound = bindings.get(name)
        if bound is None:
            return []
        module, imported = bound
        return [Occurrence(module, imported or "", _line(node))]
    chain = _js_chain(function, source)
    if chain is None:
        return []
    root_name, attrs = chain
    bound = bindings.get(root_name)
    if bound is None:
        return []
    module, imported = bound
    if imported:
        symbol = imported if not attrs else f"{imported}.{'.'.join(attrs)}"
    else:
        symbol = ".".join(attrs)
    return [Occurrence(module, symbol, _line(node))]


def _js_chain(node: Node, source: bytes) -> tuple[str, list[str]] | None:
    attrs: list[str] = []
    current = node
    while current.type == "member_expression":
        parts = [child for child in _children(current) if child.type != "."]
        if len(parts) != 2:
            return None
        if parts[1].type not in {"property_identifier", "identifier"}:
            return None
        attrs.append(_text(parts[1], source))
        current = parts[0]
    if current.type != "identifier":
        return None
    attrs.reverse()
    return _text(current, source), attrs


def _js_module(node: Node, source: bytes) -> str:
    for child in _walk(node):
        if child.type == "string_fragment":
            return _text(child, source)
    return ""


def _go(source: bytes, path: Path, root: Path) -> tuple[list[Occurrence], set[str]]:
    tree = _parser("go").parse(source)
    shadows = _go_shadow_map(tree.root_node, source)
    bindings: dict[str, str] = {}
    found: list[Occurrence] = []
    for node in _walk(tree.root_node):
        if node.type == "import_spec":
            alias, module = _go_import(node, source)
            if not module or _local(module, root):
                continue
            local = alias or module.rsplit("/", 1)[-1]
            bindings[local] = module
            found.append(Occurrence(module, "", _line(node)))
        elif node.type == "call_expression":
            found.extend(_go_call(node, source, bindings, shadows))
    return found, _go_scopes(tree.root_node, source, path, root)


def _go_import(node: Node, source: bytes) -> tuple[str | None, str]:
    alias = None
    module = ""
    for child in _children(node):
        if child.type == "package_identifier":
            alias = _text(child, source)
        elif child.type == "interpreted_string_literal":
            content = next(
                (part for part in _children(child) if part.type == "interpreted_string_literal_content"),
                None,
            )
            if content is not None:
                module = _text(content, source)
    return alias, module


def _go_call(
    node: Node,
    source: bytes,
    bindings: dict[str, str],
    shadows: dict[int, list[tuple[int, str]]],
) -> list[Occurrence]:
    selector = next((child for child in _children(node) if child.type == "selector_expression"), None)
    if selector is None:
        return []
    chain = _go_selector(selector, source)
    if chain is None:
        return []
    root_name, attrs = chain
    module = bindings.get(root_name)
    if module is None or _go_shadowed(node, root_name, shadows):
        return []
    return [Occurrence(module, ".".join(attrs), _line(node))]


def _go_selector(node: Node, source: bytes) -> tuple[str, list[str]] | None:
    attrs: list[str] = []
    current = node
    while current.type == "selector_expression":
        field = next((child for child in _children(current) if child.type == "field_identifier"), None)
        operand = next(
            (
                child
                for child in _children(current)
                if child.type in {"identifier", "selector_expression"}
            ),
            None,
        )
        if field is None or operand is None:
            return None
        attrs.append(_text(field, source))
        current = operand
    if current.type != "identifier":
        return None
    attrs.reverse()
    return _text(current, source), attrs


def _shell(source: bytes, path: Path) -> tuple[list[Occurrence], set[str], set[str]]:
    grammar = {".zsh": "zsh", ".fish": "fish"}.get(path.suffix.lower(), "bash")
    tree = _parser(grammar).parse(source)
    defined: set[str] = set()
    sources: set[str] = set()
    found: list[Occurrence] = []
    for node in _walk(tree.root_node):
        if node.type == "function_definition":
            name = next((child for child in _children(node) if child.type == "word"), None)
            if name is not None:
                defined.add(_text(name, source))
        elif node.type == "command":
            sourced = _shell_sources(node, source)
            if sourced:
                sources.update(sourced)
                continue
            for name, skips_function in _shell_command(node, source):
                found.append(Occurrence("", name, _line(node), skips_function=skips_function))
        elif node.type in _REDIRECTS:
            found.append(Occurrence("", "redirect", _line(node)))
    return found, defined, sources


def _shell_sources(node: Node, source: bytes) -> list[str]:
    """Literal paths given to ``source`` or ``.``.

    ``NVM_ENV=testing \\. file`` keeps the path. A ``\\.`` that the parser
    attaches to the previous command, as in ``: nvm.sh`` followed by
    ``\\. file``, is an include too. A path that contains a variable is not.
    One command node can hold more than one include when the parser joins them.
    """
    children = _children(node)
    found: list[str] = []
    index = 0
    while index < len(children):
        keyword = _source_keyword(children, source, index)
        if keyword is None:
            break
        path, index = _source_path(children, source, keyword + 1)
        if path:
            found.append(path)
    return found


def _source_keyword(children: list[Node], source: bytes, start: int) -> int | None:
    for index, child in enumerate(children[start:], start):
        if child.type == "command_name":
            word = next((part for part in _children(child) if part.type == "word"), None)
            if word is not None and _is_source_keyword(_text(word, source)):
                return index
        elif child.type == "word" and _is_source_keyword(_text(child, source)):
            return index
    return None


def _source_path(children: list[Node], source: bytes, start: int) -> tuple[str | None, int]:
    for index, child in enumerate(children[start:], start):
        if child.type == "variable_assignment":
            continue
        if child.type == "word" and _is_source_keyword(_text(child, source)):
            return None, index
        if child.type == "word":
            return _text(child, source).strip() or None, index + 1
        if child.type == "string":
            return _literal_string(child, source), index + 1
        return None, index + 1
    return None, len(children)


def _is_source_keyword(text: str) -> bool:
    text = text.strip()
    if text.startswith("\\"):
        text = text[1:]
    return text in _SHELL_SOURCES


def _literal_string(node: Node, source: bytes) -> str | None:
    parts: list[str] = []
    for child in _children(node):
        if child.type == '"':
            continue
        if child.type != "string_content":
            return None
        parts.append(_text(child, source))
    return "".join(parts) or None


def _shell_command(node: Node, source: bytes) -> list[tuple[str, bool]]:
    """Command names in this node, and whether each was written as ``command name``.

    ``command "${VAR}"`` and ``$VAR`` are the literal names assigned to ``VAR``
    in the enclosing function. An empty literal is ignored. A quoted prefix
    followed only by ``${NAME-}`` keeps that prefix. Any other computed value,
    or a variable assigned outside that function, is left unresolved.
    """
    name = ""
    words: list[str] = []
    for child in _children(node):
        if child.type == "command_name":
            word = next((part for part in _children(child) if part.type == "word"), None)
            if word is not None:
                name = _text(word, source)
        elif child.type == "word":
            words.append(_text(child, source))
    if not name:
        variable = _command_name_variable(node, source)
        if variable is None:
            return []
        return [(literal, False) for literal in _assigned_commands(node, source, variable)]
    if name in _SHELL_SOURCES:
        return []
    if name == "command":
        variable = _command_variable(node, source)
        if variable is not None:
            return [(literal, True) for literal in _assigned_commands(node, source, variable)]
        if words:
            return [(words[0], True)]
        return []
    if name in _WRAPPERS and words:
        return [(words[0], False)]
    return [(name, False)]


def _assigned_commands(node: Node, source: bytes, variable: str) -> list[str]:
    function = _enclosing_function(node)
    if function is None:
        return []
    literals = _literal_assignments(function, source, variable)
    if not literals:
        return []
    return list(dict.fromkeys(literals))


def _command_name_variable(node: Node, source: bytes) -> str | None:
    for child in _children(node):
        if child.type != "command_name":
            continue
        expansion = next(
            (part for part in _children(child) if part.type in {"simple_expansion", "expansion"}),
            None,
        )
        if expansion is not None:
            return _expansion_name(expansion, source)
    return None


def _command_variable(node: Node, source: bytes) -> str | None:
    arguments = [child for child in _children(node) if child.type != "command_name"]
    if not arguments:
        return None
    first = arguments[0]
    if first.type in {"simple_expansion", "expansion"}:
        return _expansion_name(first, source)
    if first.type != "string":
        return None
    inner = [child for child in _children(first) if child.type != '"']
    if len(inner) == 1 and inner[0].type in {"simple_expansion", "expansion"}:
        return _expansion_name(inner[0], source)
    return None


def _expansion_name(node: Node, source: bytes) -> str | None:
    name = next((child for child in _children(node) if child.type == "variable_name"), None)
    if name is None:
        return None
    return _text(name, source)


def _enclosing_function(node: Node) -> Node | None:
    parent = node.parent
    while parent is not None:
        if parent.type == "function_definition":
            return parent
        parent = parent.parent
    return None


def _literal_assignments(function: Node, source: bytes, variable: str) -> list[str] | None:
    names: list[str] = []
    pending = list(_children(function))
    while pending:
        node = pending.pop()
        if node.type == "function_definition":
            continue
        if node.type == "variable_assignment":
            assigned = next((child for child in _children(node) if child.type == "variable_name"), None)
            if assigned is not None and _text(assigned, source) == variable:
                literal = _assignment_literal(node, source)
                if literal is None or any(character.isspace() for character in literal):
                    return None
                if literal:
                    names.append(literal)
            continue
        pending.extend(_children(node))
    return names


def _assignment_literal(node: Node, source: bytes) -> str | None:
    value = next((child for child in _children(node) if child.type not in {"variable_name", "="}), None)
    if value is None:
        return ""
    if value.type == "word":
        return _text(value, source)
    if value.type == "raw_string":
        text = _text(value, source)
        if len(text) >= 2 and text.startswith("'") and text.endswith("'"):
            return text[1:-1]
        return None
    if value.type == "string":
        parts: list[str] = []
        for child in _children(value):
            if child.type == '"':
                continue
            if child.type == "string_content":
                parts.append(_text(child, source))
                continue
            if child.type == "expansion" and _unset_default(child, source):
                continue
            return None
        return "".join(parts)
    return None


def _unset_default(node: Node, source: bytes) -> bool:
    """True for ``${NAME-}`` or ``${NAME:-}``, an expansion that defaults to empty."""
    body = [child for child in _children(node) if child.type not in {"${", "}"}]
    if len(body) != 2 or body[0].type != "variable_name":
        return False
    return _text(body[1], source) in {"-", ":-"}


def _rust(
    source: bytes, path: Path, root: Path
) -> tuple[list[Occurrence], set[str], set[tuple[str, str]]]:
    tree = _parser("rust").parse(source)
    bindings: dict[str, tuple[str, str | None]] = {}
    defined: set[str] = set()
    found: list[Occurrence] = []
    for node in _walk(tree.root_node):
        if node.type == "function_item":
            name = next((child for child in _children(node) if child.type == "identifier"), None)
            if name is not None:
                defined.add(_text(name, source))
        elif node.type == "use_declaration":
            _rust_use(node, source, root, bindings, found)
        elif node.type == "call_expression":
            found.extend(_rust_call(node, source, bindings, defined))
    scopes, aliases = _rust_scopes(tree.root_node, source, path, root)
    return found, scopes, aliases


def _rust_use(
    node: Node,
    source: bytes,
    root: Path,
    bindings: dict[str, tuple[str, str | None]],
    found: list[Occurrence],
) -> None:
    body = next((child for child in _children(node) if child.type not in {"use", ";"}), None)
    if body is not None:
        _rust_use_tree(body, [], source, root, bindings, found, _line(node))


def _rust_use_tree(
    node: Node,
    prefix: list[str],
    source: bytes,
    root: Path,
    bindings: dict[str, tuple[str, str | None]],
    found: list[Occurrence],
    line: int,
) -> None:
    if node.type == "use_list":
        for child in _children(node):
            _rust_use_tree(child, prefix, source, root, bindings, found, line)
        return
    if node.type == "scoped_use_list":
        path = next(
            (child for child in _children(node) if child.type in {"identifier", "scoped_identifier"}),
            None,
        )
        listing = next((child for child in _children(node) if child.type == "use_list"), None)
        if path is None or listing is None:
            return
        if path.type == "identifier":
            segments = [_text(path, source)]
        else:
            segments = [part for part in _text(path, source).split("::") if part]
        for child in _children(listing):
            _rust_use_tree(child, prefix + segments, source, root, bindings, found, line)
        return
    if node.type == "use_as_clause":
        alias = next((child for child in _children(node) if child.type == "identifier"), None)
        inner = next(
            (
                child
                for child in _children(node)
                if child.type in {"identifier", "scoped_identifier"}
            ),
            None,
        )
        if alias is None or inner is None:
            return
        parts = _rust_path(inner, prefix, source)
        _rust_bind(parts, _text(alias, source), root, bindings, found, line)
        return
    if node.type == "self":
        _rust_bind(prefix, prefix[-1] if prefix else "", root, bindings, found, line)
        return
    if node.type in {"identifier", "scoped_identifier"}:
        parts = _rust_path(node, prefix, source)
        _rust_bind(parts, parts[-1] if parts else "", root, bindings, found, line)


def _rust_path(node: Node, prefix: list[str], source: bytes) -> list[str]:
    if node.type == "identifier":
        return prefix + [_text(node, source)]
    return prefix + [part for part in _text(node, source).split("::") if part]


def _rust_bind(
    parts: list[str],
    local: str,
    root: Path,
    bindings: dict[str, tuple[str, str | None]],
    found: list[Occurrence],
    line: int,
) -> None:
    if not local or not parts or parts[0] in {"crate", "self", "super"} or _local(parts[0], root):
        return
    module = "::".join(parts[:-1])
    bindings[local] = (module, parts[-1] if module else None)
    found.append(Occurrence(module or parts[0], parts[-1] if module else "", line))


def _rust_call(
    node: Node,
    source: bytes,
    bindings: dict[str, tuple[str, str | None]],
    defined: set[str],
) -> list[Occurrence]:
    target = next(
        (
            child
            for child in _children(node)
            if child.type in {"identifier", "scoped_identifier"}
        ),
        None,
    )
    if target is None:
        return []
    if target.type == "identifier":
        name = _text(target, source)
        if name in defined:
            return []
        bound = bindings.get(name)
        if bound is None:
            return []
        module, imported = bound
        return [Occurrence(module, imported or "", _line(node))]
    parts = [part for part in _text(target, source).split("::") if part]
    if not parts or parts[0] in {"crate", "self", "super"}:
        return []
    bound = bindings.get(parts[0])
    if bound is not None:
        module, imported = bound
        rest = parts[1:]
        if imported:
            rest = [imported, *rest]
        symbol = rest[-1] if rest else ""
        head = "::".join(rest[:-1])
        full = f"{module}::{head}" if head else module
        return [Occurrence(full, symbol, _line(node))]
    if len(parts) == 1:
        return []
    return [Occurrence("::".join(parts[:-1]), parts[-1], _line(node))]


def _csharp(source: bytes) -> tuple[list[Occurrence], set[str]]:
    """Type calls such as ``File.ReadAllText`` and ``System.IO.File.WriteAllText``.

    A lowercase receiver is a local value (``file.Read``) and is left alone.
    ``using Alias = Namespace`` resolves that alias. A bare ``WriteLine()`` is not
    a type call.
    """
    tree = _parser("csharp").parse(source)
    bindings: dict[str, str] = {}
    found: list[Occurrence] = []
    for node in _walk(tree.root_node):
        if node.type == "using_directive":
            _csharp_using(node, source, bindings, found)
        elif node.type == "invocation_expression":
            found.extend(_csharp_call(node, source, bindings))
        elif node.type == "object_creation_expression":
            found.extend(_csharp_new(node, source, bindings))
    return found, _csharp_scopes(tree.root_node, source)


def _csharp_using(
    node: Node,
    source: bytes,
    bindings: dict[str, str],
    found: list[Occurrence],
) -> None:
    named = [child for child in _children(node) if child.type in {"identifier", "qualified_name"}]
    if any(child.type == "=" for child in _children(node)) and len(named) >= 2:
        bindings[_text(named[0], source)] = _text(named[1], source)
        found.append(Occurrence(_text(named[1], source), "", _line(node)))
        return
    qualified = next((child for child in named if child.type == "qualified_name"), None)
    if qualified is None and named:
        qualified = named[-1]
    if qualified is None:
        return
    found.append(Occurrence(_text(qualified, source), "", _line(node)))


def _csharp_call(
    node: Node,
    source: bytes,
    bindings: dict[str, str],
) -> list[Occurrence]:
    member = next((child for child in _children(node) if child.type == "member_access_expression"), None)
    if member is None:
        return []
    names = _csharp_chain(member, source)
    bound = _csharp_bound(names, bindings)
    if bound is None:
        return []
    module, symbol = bound
    return [Occurrence(module, symbol, _line(node))]


def _csharp_new(
    node: Node,
    source: bytes,
    bindings: dict[str, str],
) -> list[Occurrence]:
    named = next(
        (child for child in _children(node) if child.type in {"qualified_name", "generic_name"}),
        None,
    )
    if named is None:
        return []
    names = _csharp_type_names(named, source)
    if not names or not names[0][:1].isupper():
        return []
    head = bindings.get(names[0], names[0])
    return [Occurrence(".".join([head, *names[1:]]), "", _line(node))]


def _csharp_bound(names: list[str] | None, bindings: dict[str, str]) -> tuple[str, str] | None:
    if not names or len(names) < 2 or not names[0][:1].isupper():
        return None
    head = bindings.get(names[0], names[0])
    module = ".".join([head, *names[1:-1]])
    return module, names[-1]


def _csharp_chain(node: Node, source: bytes) -> list[str] | None:
    names: list[str] = []
    current = node
    while current.type == "member_access_expression":
        parts = [child for child in _children(current) if child.type != "."]
        if len(parts) != 2 or parts[1].type != "identifier":
            return None
        names.append(_text(parts[1], source))
        current = parts[0]
    if current.type == "alias_qualified_name":
        identifiers = [child for child in _children(current) if child.type == "identifier"]
        if len(identifiers) < 2 or _text(identifiers[0], source) != "global":
            return None
        names.append(_text(identifiers[-1], source))
    elif current.type == "identifier":
        names.append(_text(current, source))
    else:
        return None
    names.reverse()
    return names


def _csharp_type_names(node: Node, source: bytes) -> list[str]:
    if node.type == "generic_name":
        named = next((child for child in _children(node) if child.type in {"identifier", "qualified_name"}), None)
        return _csharp_type_names(named, source) if named is not None else []
    if node.type == "identifier":
        return [_text(node, source)]
    names: list[str] = []
    for child in _children(node):
        if child.type in {"identifier", "qualified_name"}:
            names.extend(_csharp_type_names(child, source))
    return names


_CSHARP_TYPES = {
    "class_declaration",
    "struct_declaration",
    "record_declaration",
    "interface_declaration",
}


def _csharp_scopes(root: Node, source: bytes) -> set[str]:
    names: set[str] = set()
    for node in _walk(root):
        if node.type in {"namespace_declaration", "file_scoped_namespace_declaration"}:
            qualified = _csharp_namespace(node, source)
            if qualified:
                names.add(qualified)
        if node.type != "method_declaration":
            continue
        children = _children(node)
        parameter = next((child for child in children if child.type == "parameter_list"), None)
        if parameter is None:
            continue
        index = children.index(parameter)
        if index == 0 or children[index - 1].type != "identifier":
            continue
        method = _text(children[index - 1], source)
        qualified, simple = _csharp_owner(node, source)
        if not simple:
            continue
        names.add(simple)
        names.add(_member(simple, method))
        if qualified != simple:
            names.add(qualified)
            names.add(_member(qualified, method))
    return names


def _csharp_namespace(node: Node, source: bytes) -> str:
    named = next(
        (child for child in _children(node) if child.type in {"identifier", "qualified_name"}),
        None,
    )
    if named is None:
        return ""
    parents: list[str] = []
    current = node.parent
    while current is not None:
        if current.type in {"namespace_declaration", "file_scoped_namespace_declaration"}:
            outer = next(
                (child for child in _children(current) if child.type in {"identifier", "qualified_name"}),
                None,
            )
            if outer is not None:
                parents.append(_text(outer, source))
        current = current.parent
    parents.reverse()
    name = _text(named, source)
    return ".".join([*parents, name]) if parents else name


def _csharp_owner(node: Node, source: bytes) -> tuple[str, str]:
    simple = ""
    namespaces: list[str] = []
    current = node.parent
    while current is not None:
        if not simple and current.type in _CSHARP_TYPES:
            ident = next((child for child in _children(current) if child.type == "identifier"), None)
            if ident is not None:
                simple = _text(ident, source)
        elif current.type in {"namespace_declaration", "file_scoped_namespace_declaration"}:
            named = next(
                (child for child in _children(current) if child.type in {"identifier", "qualified_name"}),
                None,
            )
            if named is not None:
                namespaces.append(_text(named, source))
        current = current.parent
    namespaces.reverse()
    if not simple:
        return "", ""
    namespace = ".".join(namespaces)
    return (f"{namespace}.{simple}" if namespace else simple), simple


def _java(source: bytes) -> tuple[list[Occurrence], set[str]]:
    return _jvm(source, "java", "package_declaration", "import_declaration", "scoped_identifier", "method_invocation")


def _kotlin(source: bytes) -> tuple[list[Occurrence], set[str]]:
    return _jvm_kotlin(source)


def _jvm(
    source: bytes,
    grammar: str,
    package_type: str,
    import_type: str,
    name_type: str,
    call_type: str,
) -> tuple[list[Occurrence], set[str]]:
    tree = _parser(grammar).parse(source)
    bindings: dict[str, str] = {}
    found: list[Occurrence] = []
    package = _declared_package(tree.root_node, source, package_type, {name_type, "identifier"})
    declared = _jvm_members(tree.root_node, source, package)
    for node in _walk(tree.root_node):
        if node.type == import_type:
            _java_import(node, source, name_type, bindings, found)
        elif node.type == call_type:
            found.extend(_java_call(node, source, bindings))
        elif node.type == "object_creation_expression":
            type_name = next((child for child in _children(node) if child.type == "type_identifier"), None)
            if type_name is None:
                continue
            bound = bindings.get(_text(type_name, source))
            if bound is not None:
                found.append(Occurrence(bound, "", _line(node)))
    return found, declared


def _java_import(
    node: Node,
    source: bytes,
    name_type: str,
    bindings: dict[str, str],
    found: list[Occurrence],
) -> None:
    named = next((child for child in _children(node) if child.type == name_type), None)
    if named is None:
        return
    qualified = _text(named, source)
    if any(child.type == "asterisk" for child in _children(node)):
        found.append(Occurrence(qualified, "", _line(node)))
        return
    simple = qualified.rsplit(".", 1)[-1]
    bindings[simple] = qualified
    found.append(Occurrence(qualified, "", _line(node)))


def _java_call(
    node: Node,
    source: bytes,
    bindings: dict[str, str],
) -> list[Occurrence]:
    names = [child for child in _children(node) if child.type == "identifier"]
    if len(names) < 2:
        return []
    bound = bindings.get(_text(names[0], source))
    if bound is None:
        return []
    return [Occurrence(bound, _text(names[-1], source), _line(node))]


def _jvm_kotlin(source: bytes) -> tuple[list[Occurrence], set[str]]:
    tree = _parser("kotlin").parse(source)
    bindings: dict[str, str] = {}
    found: list[Occurrence] = []
    package = _declared_package(tree.root_node, source, "package_header", {"identifier"})
    declared = _kotlin_members(tree.root_node, source, package)
    for node in _walk(tree.root_node):
        if node.type == "import_header":
            named = next((child for child in _children(node) if child.type == "identifier"), None)
            if named is None:
                continue
            qualified = _text(named, source)
            alias = next((child for child in _children(node) if child.type == "import_alias"), None)
            if alias is None:
                local = qualified.rsplit(".", 1)[-1]
            else:
                renamed = next(
                    (child for child in _children(alias) if child.type == "type_identifier"),
                    None,
                )
                local = _text(renamed, source) if renamed is not None else qualified.rsplit(".", 1)[-1]
            bindings[local] = qualified
            found.append(Occurrence(qualified, "", _line(node)))
        elif node.type == "call_expression":
            found.extend(_kotlin_call(node, source, bindings))
    return found, declared


_JVM_TYPES = {
    "class_declaration",
    "interface_declaration",
    "enum_declaration",
    "record_declaration",
    "annotation_type_declaration",
}
_KOTLIN_TYPES = {"class_declaration", "object_declaration", "interface_declaration"}


def _member(module: str, symbol: str) -> str:
    return f"{module}\0{symbol}"


def _jvm_members(root: Node, source: bytes, package: str) -> set[str]:
    names: set[str] = set()
    stack: list[tuple[Node, str]] = [(root, "")]
    while stack:
        node, enclosing = stack.pop()
        if node.type in _JVM_TYPES:
            ident = next((child for child in _children(node) if child.type == "identifier"), None)
            if ident is None:
                continue
            simple = _text(ident, source)
            qualified = f"{enclosing}.{simple}" if enclosing else _qualify(package, simple)
            names.add(qualified)
            stack.extend((child, qualified) for child in _children(node))
            continue
        if node.type == "method_declaration" and enclosing:
            ident = next((child for child in _children(node) if child.type == "identifier"), None)
            if ident is not None:
                names.add(_member(enclosing, _text(ident, source)))
        elif node.type == "field_declaration" and enclosing:
            for declarator in _children(node):
                if declarator.type != "variable_declarator":
                    continue
                simple = _declared_name(declarator, source, {"identifier"})
                if simple:
                    names.add(_member(enclosing, simple))
        elif node.type == "enum_constant" and enclosing:
            simple = _declared_name(node, source, {"identifier"})
            if simple:
                names.add(_member(enclosing, simple))
                names.add(f"{enclosing}.{simple}")
                stack.extend((child, f"{enclosing}.{simple}") for child in _children(node))
                continue
        stack.extend((child, enclosing) for child in _children(node))
    return names


def _kotlin_members(root: Node, source: bytes, package: str) -> set[str]:
    names: set[str] = set()
    stack: list[tuple[Node, str]] = [(root, "")]
    while stack:
        node, enclosing = stack.pop()
        if node.type in _KOTLIN_TYPES:
            ident = next(
                (child for child in _children(node) if child.type in {"type_identifier", "identifier"}),
                None,
            )
            if ident is None:
                continue
            simple = _text(ident, source)
            qualified = f"{enclosing}.{simple}" if enclosing else _qualify(package, simple)
            names.add(qualified)
            stack.extend((child, qualified) for child in _children(node))
            continue
        if node.type == "companion_object":
            simple = _declared_name(node, source, {"type_identifier", "identifier"}) or "Companion"
            qualified = f"{enclosing}.{simple}" if enclosing else _qualify(package, simple)
            names.add(qualified)
            stack.extend((child, qualified) for child in _children(node))
            continue
        if node.type == "function_declaration":
            simple = _declared_name(node, source, {"simple_identifier", "identifier"})
            if simple:
                if enclosing:
                    names.add(_member(enclosing, simple))
                else:
                    _top_level(names, package, simple)
        elif node.type == "property_declaration":
            declaration = next((child for child in _children(node) if child.type == "variable_declaration"), None)
            simple = _declared_name(declaration, source, {"simple_identifier", "identifier"}) if declaration else None
            if simple:
                if enclosing:
                    names.add(_member(enclosing, simple))
                else:
                    _top_level(names, package, simple)
        elif node.type == "enum_entry" and enclosing:
            simple = _declared_name(node, source, {"simple_identifier", "identifier"})
            if simple:
                names.add(_member(enclosing, simple))
                names.add(f"{enclosing}.{simple}")
                stack.extend((child, f"{enclosing}.{simple}") for child in _children(node))
                continue
        stack.extend((child, enclosing) for child in _children(node))
    return names


def _declared_name(node: Node, source: bytes, types: set[str]) -> str | None:
    ident = next((child for child in _children(node) if child.type in types), None)
    if ident is None:
        return None
    return _text(ident, source)


def _qualify(package: str, name: str) -> str:
    return f"{package}.{name}" if package else name


def _top_level(names: set[str], package: str, simple: str) -> None:
    """A file-level function or property, also as a member of its package.

    The dotted name matches an import. The member key matches a call on that
    property, such as ``connection.socket()``.
    """
    names.add(_qualify(package, simple))
    if package:
        names.add(_member(package, simple))


def _go_scopes(root: Node, source: bytes, path: Path, repo: Path) -> set[str]:
    import_path = _go_import_path(path, repo)
    if not import_path:
        return set()
    names = {import_path}
    for node in _children(root):
        if node.type in {"function_declaration", "method_declaration"}:
            ident = next(
                (child for child in _children(node) if child.type in {"identifier", "field_identifier"}),
                None,
            )
            if ident is not None:
                names.add(_member(import_path, _text(ident, source)))
        elif node.type in {"var_declaration", "const_declaration"}:
            _go_value_names(node, source, import_path, names)
        elif node.type == "type_declaration":
            _go_type_names(node, source, import_path, names)
    _go_text_names(source, import_path, names)
    return names


def _go_type_names(node: Node, source: bytes, import_path: str, names: set[str]) -> None:
    for child in _children(node):
        if child.type == "type_spec":
            named = next(
                (part for part in _children(child) if part.type == "type_identifier"),
                None,
            )
            if named is not None:
                names.add(_member(import_path, _text(named, source)))
        elif child.type == "type_spec_list":
            _go_type_names(child, source, import_path, names)


def _go_text_names(source: bytes, import_path: str, names: set[str]) -> None:
    """Top-level names, including a method the grammar drops at a type parameter."""
    for match in _GO_TOP_LEVEL.finditer(source):
        names.add(_member(import_path, match.group(1).decode("utf-8")))


def _go_shadow_map(root: Node, source: bytes) -> dict[int, list[tuple[int, str]]]:
    """Names each function assigns, collected before calls are read.

    A second walk of a function while the file walk is in progress reuses one
    tree cursor and drops the receiver.
    """
    found: dict[int, list[tuple[int, str]]] = {}
    for node in _walk(root):
        declared = _go_declared(node, source)
        if not declared:
            continue
        owner = _go_enclosing(node)
        if owner is None:
            continue
        found.setdefault(owner.id, []).extend((node.start_byte, name) for name in declared)
    return found


def _go_shadowed(node: Node, name: str, shadows: dict[int, list[tuple[int, str]]]) -> bool:
    """True when an enclosing function assigned ``name`` before the call."""
    current = node.parent
    while current is not None:
        if current.type in {"function_declaration", "method_declaration", "func_literal"}:
            if any(
                declared == name and start < node.start_byte
                for start, declared in shadows.get(current.id, ())
            ):
                return True
        current = current.parent
    return False


def _go_enclosing(node: Node) -> Node | None:
    current = node.parent
    while current is not None and current.type not in {
        "function_declaration",
        "method_declaration",
        "func_literal",
    }:
        current = current.parent
    return current


def _go_declared(node: Node, source: bytes) -> list[str]:
    if node.type == "short_var_declaration":
        left = next((child for child in _children(node) if child.type == "expression_list"), None)
        return _go_ident_names(left, source) if left is not None else []
    if node.type == "range_clause":
        left = next((child for child in _children(node) if child.type == "expression_list"), None)
        return _go_ident_names(left, source) if left is not None else []
    if node.type in {"parameter_declaration", "var_spec", "const_spec"}:
        return [
            _text(child, source)
            for child in _children(node)
            if child.type == "identifier" and _text(child, source) != "_"
        ]
    return []


def _go_ident_names(node: Node, source: bytes) -> list[str]:
    return [
        _text(child, source)
        for child in _children(node)
        if child.type == "identifier" and _text(child, source) != "_"
    ]


def _go_value_names(node: Node, source: bytes, import_path: str, names: set[str]) -> None:
    """Package-level ``var`` and ``const`` names. A local declaration is not one of these."""
    for child in _children(node):
        if child.type in {"var_spec", "const_spec"}:
            for part in _children(child):
                if part.type == "identifier":
                    names.add(_member(import_path, _text(part, source)))
        elif child.type in {"var_spec_list", "const_spec_list"}:
            _go_value_names(child, source, import_path, names)


def _go_import_path(path: Path, root: Path) -> str:
    current = path.parent
    while True:
        manifest = current / "go.mod"
        if manifest.is_file():
            module = _go_module_line(manifest.read_text(encoding="utf-8", errors="replace"))
            if module:
                relative = path.parent.relative_to(current).as_posix()
                return module if relative == "." else f"{module}/{relative}"
        if current == root or root not in current.parents:
            return ""
        current = current.parent


def _go_module_line(text: str) -> str:
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "module":
            return parts[1].strip('"')
    return ""


def _js_scopes(root: Node, source: bytes, path: Path, repo: Path) -> set[str]:
    package, package_root = _npm_package(path, repo)
    if not package:
        return set()
    modules = _js_module_ids(package, path.relative_to(package_root).as_posix())
    names = set(modules)
    for exported in _js_exports(root, source):
        names.update(_member(module, exported) for module in modules)
    return names


def _js_exports(root: Node, source: bytes) -> set[str]:
    names: set[str] = set()
    for node in _walk(root):
        if node.type != "export_statement":
            continue
        for child in _children(node):
            if child.type in {"function_declaration", "class_declaration", "generator_function_declaration"}:
                _add_identifier(child, source, names)
            elif child.type in {"lexical_declaration", "variable_declaration"}:
                for declarator in _children(child):
                    if declarator.type == "variable_declarator":
                        _add_identifier(declarator, source, names)
            elif child.type == "export_clause":
                for specifier in _children(child):
                    if specifier.type == "export_specifier":
                        identifiers = [part for part in _children(specifier) if part.type == "identifier"]
                        if identifiers:
                            names.add(_text(identifiers[-1], source))
    return names


def _add_identifier(node: Node, source: bytes, names: set[str]) -> None:
    ident = next((child for child in _children(node) if child.type == "identifier"), None)
    if ident is not None:
        names.add(_text(ident, source))


def _js_module_ids(package: str, relative: str) -> set[str]:
    for suffix in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"):
        if relative.endswith(suffix):
            relative = relative[: -len(suffix)]
            break
    ids = {f"{package}/{relative}"}
    if relative == "index":
        ids.add(package)
    elif relative.endswith("/index"):
        ids.add(f"{package}/{relative[: -len('/index')]}")
    return ids


def _npm_package(path: Path, root: Path) -> tuple[str, Path]:
    current = path.parent
    while True:
        manifest = current / "package.json"
        if manifest.is_file():
            name = _json_name(manifest)
            if name:
                return name, current
        if current == root or root not in current.parents:
            return "", root
        current = current.parent


def _json_name(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    name = data.get("name") if isinstance(data, dict) else None
    return name if isinstance(name, str) else ""


def _rust_scopes(
    root: Node, source: bytes, path: Path, repo: Path
) -> tuple[set[str], set[tuple[str, str]]]:
    """Declared items, under the Cargo path and the path written in source.

    A hyphen in the package name is also stored with underscores. ``src/lib.rs``
    stores each ``pub use`` name at the crate root, and each
    ``pub extern crate name as alias`` as a path alias. An inherent method is
    stored on its type, so ``WalkBuilder::new`` matches.
    """
    prefix = _rust_module(path, repo)
    if not prefix:
        return set(), set()
    prefixes = _rust_prefixes(prefix)
    names: set[str] = set()
    aliases: set[tuple[str, str]] = set()
    crate_root = _cargo_package(path, repo)[1]
    is_lib = path.relative_to(crate_root).as_posix() == "src/lib.rs"
    for node in _walk(root):
        if node.type == "function_item":
            ident = next((child for child in _children(node) if child.type == "identifier"), None)
            if ident is not None:
                _rust_add(names, prefixes, _text(ident, source))
        elif node.type in {"struct_item", "enum_item", "trait_item"}:
            named = next((child for child in _children(node) if child.type == "type_identifier"), None)
            if named is not None:
                _rust_add(names, prefixes, _text(named, source))
            if node.type == "enum_item":
                _rust_variants(node, source, names)
        elif node.type == "impl_item":
            _rust_methods(node, source, names)
        elif is_lib and node.type == "use_declaration" and _rust_is_pub(node, source):
            for exported in _rust_export_names(node, source):
                _rust_add(names, prefixes, exported)
        elif is_lib and node.type == "extern_crate_declaration" and _rust_is_pub(node, source):
            alias = _rust_extern_alias(node, source, prefixes[0].replace("-", "_"))
            if alias is not None:
                aliases.add(alias)
    return names, aliases


def _rust_prefixes(prefix: str) -> tuple[str, ...]:
    """The Cargo path and, when the package name has a hyphen, the source path."""
    head, sep, tail = prefix.partition("::")
    plain = head.replace("-", "_")
    if plain == head:
        return (prefix,)
    return (prefix, f"{plain}::{tail}" if sep else plain)


def _rust_add(names: set[str], prefixes: tuple[str, ...], symbol: str) -> None:
    for prefix in prefixes:
        names.add(_member(prefix, symbol))


def _rust_is_pub(node: Node, source: bytes) -> bool:
    visible = next((child for child in _children(node) if child.type == "visibility_modifier"), None)
    return visible is not None and _text(visible, source) == "pub"


def _rust_variants(node: Node, source: bytes, names: set[str]) -> None:
    named = next((child for child in _children(node) if child.type == "type_identifier"), None)
    if named is None:
        return
    enum = _text(named, source)
    for variant in _walk(node):
        if variant.type != "enum_variant":
            continue
        ident = next((child for child in _children(variant) if child.type == "identifier"), None)
        if ident is not None:
            names.add(_member(enum, _text(ident, source)))


def _rust_methods(node: Node, source: bytes, names: set[str]) -> None:
    """Methods of ``impl Type``, not ``impl Trait for Type``."""
    if any(child.type == "for" for child in _children(node)):
        return
    owner = ""
    for child in _children(node):
        if child.type == "type_identifier":
            owner = _text(child, source)
            break
        if child.type == "generic_type":
            named = next((part for part in _children(child) if part.type == "type_identifier"), None)
            owner = _text(named, source) if named is not None else ""
            break
    if not owner:
        return
    body = next((child for child in _children(node) if child.type == "declaration_list"), None)
    if body is None:
        return
    for child in _children(body):
        if child.type != "function_item":
            continue
        ident = next((part for part in _children(child) if part.type == "identifier"), None)
        if ident is not None:
            names.add(_member(owner, _text(ident, source)))


def _rust_export_names(node: Node, source: bytes) -> list[str]:
    """Names a ``pub use`` adds to this module."""
    body = next((child for child in _children(node) if child.type not in {"visibility_modifier", "use", ";"}), None)
    if body is None:
        return []
    return _rust_use_names(body, source)


def _rust_use_names(node: Node, source: bytes) -> list[str]:
    if node.type == "use_list":
        names: list[str] = []
        for child in _children(node):
            names.extend(_rust_use_names(child, source))
        return names
    if node.type == "scoped_use_list":
        path = next(
            (child for child in _children(node) if child.type in {"identifier", "scoped_identifier", "crate"}),
            None,
        )
        listing = next((child for child in _children(node) if child.type == "use_list"), None)
        if listing is None:
            return []
        names = []
        for child in _children(listing):
            if child.type == "self" and path is not None and path.type == "identifier":
                names.append(_text(path, source))
            else:
                names.extend(_rust_use_names(child, source))
        return names
    if node.type == "use_as_clause":
        renamed = [child for child in _children(node) if child.type == "identifier"]
        return [_text(renamed[-1], source)] if renamed else []
    if node.type == "identifier":
        return [_text(node, source)]
    if node.type == "scoped_identifier":
        parts = [part for part in _text(node, source).split("::") if part]
        return [parts[-1]] if parts else []
    return []


def _rust_extern_alias(node: Node, source: bytes, crate: str) -> tuple[str, str] | None:
    """``pub extern crate grep_cli as cli`` in crate ``grep`` is ``("grep::cli", "grep_cli")``."""
    idents = [child for child in _children(node) if child.type == "identifier"]
    if not idents or not crate:
        return None
    target = _text(idents[0], source)
    alias = _text(idents[-1], source)
    if not target or not alias:
        return None
    return f"{crate}::{alias}", target


def _rust_module(path: Path, root: Path) -> str:
    crate, crate_root = _cargo_package(path, root)
    if not crate:
        return ""
    relative = path.relative_to(crate_root).as_posix()
    if not relative.startswith("src/") or not relative.endswith(".rs"):
        return ""
    rest = relative[len("src/") : -len(".rs")]
    if rest in {"lib", "main"}:
        return crate
    if rest.endswith("/mod"):
        rest = rest[: -len("/mod")]
    return f"{crate}::{rest.replace('/', '::')}" if rest else crate


def _cargo_package(path: Path, root: Path) -> tuple[str, Path]:
    current = path.parent
    while True:
        manifest = current / "Cargo.toml"
        if manifest.is_file():
            name = _cargo_name(manifest)
            if name:
                return name, current
        if current == root or root not in current.parents:
            return "", root
        current = current.parent


def _cargo_name(path: Path) -> str:
    section = ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.split("#", 1)[0].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped.strip("[]").strip()
            continue
        if section == "package" and stripped.startswith("name") and "=" in stripped:
            return stripped.split("=", 1)[1].strip().strip("\"'")
    return ""


def _declared_package(root: Node, source: bytes, node_type: str, name_types: set[str]) -> str:
    for node in _walk(root):
        if node.type != node_type:
            continue
        named = next((child for child in _children(node) if child.type in name_types), None)
        if named is not None:
            return _text(named, source)
    return ""


def _kotlin_call(
    node: Node,
    source: bytes,
    bindings: dict[str, str],
) -> list[Occurrence]:
    target = next(
        (
            child
            for child in _children(node)
            if child.type in {"simple_identifier", "navigation_expression"}
        ),
        None,
    )
    if target is None:
        return []
    if target.type == "simple_identifier":
        bound = bindings.get(_text(target, source))
        if bound is None:
            return []
        return [Occurrence(bound, "", _line(node))]
    root = next((child for child in _children(target) if child.type == "simple_identifier"), None)
    suffix = next((child for child in _children(target) if child.type == "navigation_suffix"), None)
    if root is None or suffix is None:
        return []
    bound = bindings.get(_text(root, source))
    if bound is None:
        return []
    method = _text(suffix, source).lstrip(".")
    return [Occurrence(bound, method, _line(node))]


def _c_family(
    source: bytes, language: str, path: Path, root: Path
) -> tuple[list[Occurrence], set[str], set[str]]:
    tree = _parser(language).parse(source)
    local, exported, scopes = _c_defined(tree.root_node, source)
    macros = _macro_names(tree.root_node, source)
    found: list[Occurrence] = []
    for node in _walk(tree.root_node):
        if node.type == "preproc_include":
            header = _c_header(node, source, path, root)
            if header:
                found.append(Occurrence(header, "", _line(node)))
        elif node.type == "call_expression":
            qualifier, name = _c_call(node, source)
            if name and name not in local:
                if qualifier:
                    found.append(Occurrence(qualifier, name, _line(node)))
                else:
                    found.append(Occurrence("", name, _line(node), owner=_enclosing_class(node, source)))
        elif node.type in {"preproc_def", "preproc_function_def"}:
            line = _line(node)
            for qualifier, name in _macro_body_calls(node, source, language):
                if name not in local and name not in macros:
                    found.append(Occurrence(qualifier, name, line))
    return found, exported, scopes


def _c_defined(root: Node, source: bytes) -> tuple[set[str], set[str], set[str]]:
    """Names defined in this file, and the subset that is visible to other files."""
    local: set[str] = set()
    exported: set[str] = set()
    scopes: set[str] = set()
    for node in _walk(root):
        if node.type == "function_declarator":
            klass, text = _declarator_method(node, source)
            if not text:
                continue
            if klass:
                scopes.add(_member(klass, text))
                continue
            local.add(text)
            owner = _c_owner(node)
            if owner is not None and not _has_static(owner, source):
                exported.add(text)
        elif node.type in {"preproc_def", "preproc_function_def"} and not _macro_renames(node, source):
            name = next((child for child in _children(node) if child.type == "identifier"), None)
            if name is not None:
                text = _text(name, source)
                local.add(text)
                exported.add(text)
    return local, exported, scopes


def _macro_names(root: Node, source: bytes) -> set[str]:
    names: set[str] = set()
    for node in _walk(root):
        if node.type not in {"preproc_def", "preproc_function_def"}:
            continue
        ident = next((child for child in _children(node) if child.type == "identifier"), None)
        if ident is not None:
            names.add(_text(ident, source))
    return names


def _macro_body_calls(node: Node, source: bytes, language: str) -> list[tuple[str, str]]:
    """Calls in the replacement text, which tree-sitter leaves unparsed."""
    arg = next((child for child in _children(node) if child.type == "preproc_arg"), None)
    if arg is None:
        return []
    body = _text(arg, source).replace("\\\r\n", "\n").replace("\\\n", "\n").strip()
    if not body:
        return []
    params = _macro_params(node, source)
    wrapped = f"void findio_macro(void) {{\n{body};\n}}\n".encode()
    names: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for call in _walk(_parser(language).parse(wrapped).root_node):
        if call.type != "call_expression":
            continue
        qualifier, name = _c_call(call, wrapped)
        key = (qualifier, name)
        if name and name not in params and key not in seen:
            seen.add(key)
            names.append(key)
    return names


def _macro_params(node: Node, source: bytes) -> set[str]:
    params = next((child for child in _children(node) if child.type == "preproc_params"), None)
    if params is None:
        return set()
    return {_text(child, source) for child in _children(params) if child.type == "identifier"}


def _macro_renames(node: Node, source: bytes) -> bool:
    """True when the body is only another identifier, so the call keeps that name."""
    arg = next((child for child in _children(node) if child.type == "preproc_arg"), None)
    if arg is None:
        return False
    text = _text(arg, source).replace("\\\n", "").strip()
    while len(text) >= 2 and text[0] == "(" and text[-1] == ")":
        text = text[1:-1].strip()
    return text.isidentifier()


def _c_owner(node: Node) -> Node | None:
    current = node.parent
    while current is not None and current.type in {"pointer_declarator", "reference_declarator"}:
        current = current.parent
    if current is not None and current.type in {"declaration", "function_definition"}:
        return current
    return None


def _has_static(node: Node, source: bytes) -> bool:
    return any(
        child.type == "storage_class_specifier" and _text(child, source) == "static"
        for child in _children(node)
    )


def _c_header(node: Node, source: bytes, path: Path, root: Path) -> str:
    for child in _children(node):
        if child.type == "system_lib_string":
            name = _text(child, source).strip("<>")
            if _in_repo(name, root):
                return ""
            return name
        if child.type == "string_literal":
            content = next((part for part in _children(child) if part.type == "string_content"), None)
            if content is None:
                return ""
            name = _text(content, source)
            if _nearby(name, path, root):
                return ""
            return name
    return ""


def _c_callee(node: Node, source: bytes) -> str:
    return _c_call(node, source)[1]


def _c_call(node: Node, source: bytes) -> tuple[str, str]:
    """Class and method of a qualified call, or a bare name with an empty class."""
    for child in _children(node):
        if child.type == "identifier":
            return "", _text(child, source)
        if child.type == "qualified_identifier":
            parsed = _qualified_method(child, source)
            if parsed is not None:
                return parsed
    return "", ""


def _declarator_method(node: Node, source: bytes) -> tuple[str, str]:
    """Class and name of a method declarator. The class is empty for a free function."""
    for child in _children(node):
        if child.type == "identifier":
            return "", _text(child, source)
        if child.type == "field_identifier":
            return _class_of(node, source), _text(child, source)
        if child.type == "qualified_identifier":
            parsed = _qualified_method(child, source)
            if parsed is not None:
                return parsed
    return "", ""


def _qualified_method(node: Node, source: bytes) -> tuple[str, str] | None:
    """Immediate class and method from ``StreamCopier::copyStream`` or ``Poco::StreamCopier::copyStream``.

    ``::CreateFileW`` is the global function, so the class is empty.
    """
    nested = next((child for child in _children(node) if child.type == "qualified_identifier"), None)
    if nested is not None:
        return _qualified_method(nested, source)
    name = next((child for child in _children(node) if child.type == "identifier"), None)
    if name is None:
        return None
    qualifier = next(
        (child for child in _children(node) if child.type in {"namespace_identifier", "type_identifier"}),
        None,
    )
    if qualifier is None:
        if any(child.type == "::" for child in _children(node)):
            return "", _text(name, source)
        return None
    return _text(qualifier, source), _text(name, source)


def _class_of(node: Node, source: bytes) -> str:
    current = node.parent
    while current is not None:
        if current.type in {"class_specifier", "struct_specifier"}:
            named = next((child for child in _children(current) if child.type == "type_identifier"), None)
            return _text(named, source) if named is not None else ""
        current = current.parent
    return ""


def _defined_declarator(node: Node) -> Node | None:
    """The function declarator of a definition, beneath a reference or pointer return type."""
    for child in _children(node):
        if child.type == "function_declarator":
            return child
        if child.type in {"pointer_declarator", "reference_declarator"}:
            found = _defined_declarator(child)
            if found is not None:
                return found
    return None


def _enclosing_class(node: Node, source: bytes) -> str:
    """Class of the function that contains this call, or empty for a free function."""
    current = node.parent
    while current is not None:
        if current.type != "function_definition":
            current = current.parent
            continue
        declarator = _defined_declarator(current)
        if declarator is not None:
            klass, _name = _declarator_method(declarator, source)
            if klass:
                return klass
        container = current.parent
        while container is not None and container.type not in {
            "class_specifier",
            "struct_specifier",
            "function_definition",
        }:
            container = container.parent
        if container is not None and container.type in {"class_specifier", "struct_specifier"}:
            named = next((child for child in _children(container) if child.type == "type_identifier"), None)
            if named is not None:
                return _text(named, source)
        return ""
    return ""


def _php(source: bytes, path: Path, root: Path) -> tuple[list[Occurrence], set[str]]:
    tree = _parser("php").parse(source)
    defined: set[str] = set()
    aliases: dict[str, str] = {}
    found: list[Occurrence] = []
    for node in _walk(tree.root_node):
        if node.type == "function_definition":
            name = next((child for child in _children(node) if child.type == "name"), None)
            if name is not None:
                defined.add(_text(name, source))
        elif node.type == "namespace_use_declaration":
            _php_use(node, source, aliases)
        elif node.type == "include_expression":
            included = _literal_include(node, source)
            if included and not _nearby(included, path, root):
                found.append(Occurrence(included, "", _line(node)))
        elif node.type == "function_call_expression":
            called = _php_called(node, source)
            if called is None:
                continue
            called = aliases.get(called, called)
            if called not in defined:
                found.append(Occurrence("", called, _line(node)))
    return found, defined


def _php_called(node: Node, source: bytes) -> str | None:
    """The global function a call names.

    ``fopen()`` and ``\\fopen()`` are that function. ``Foo\\fopen()`` is not.
    """
    for child in _children(node):
        if child.type == "name":
            return _text(child, source)
        if child.type != "qualified_name":
            continue
        parts = _children(child)
        names = [part for part in parts if part.type == "name"]
        if names and all(part.type in {"\\", "name"} for part in parts):
            return _text(names[0], source)
    return None


def _php_use(node: Node, source: bytes, aliases: dict[str, str]) -> None:
    prefix = ""
    for child in _children(node):
        if child.type == "namespace_name":
            prefix = _text(child, source).strip("\\")
        elif child.type == "namespace_use_clause":
            _php_use_clause(child, source, prefix, aliases)
        elif child.type == "namespace_use_group":
            for clause in _children(child):
                if clause.type == "namespace_use_clause":
                    _php_use_clause(clause, source, prefix, aliases)


def _php_use_clause(clause: Node, source: bytes, prefix: str, aliases: dict[str, str]) -> None:
    text = _text(clause, source).removeprefix("function ").strip()
    original, separator, alias = text.rpartition(" as ")
    if not separator:
        original = text
        alias = original.rsplit("\\", 1)[-1]
    if prefix and "\\" not in original:
        original = f"{prefix}\\{original}"
    aliases[alias] = original


def _ruby(
    source: bytes, path: Path, root: Path
) -> tuple[list[Occurrence], set[str], set[str]]:
    functions: set[str] = set()
    scopes: set[str] = set()
    found: list[Occurrence] = []
    _ruby_walk(
        _parser("ruby").parse(source).root_node,
        source,
        path,
        root,
        (),
        set(),
        functions,
        scopes,
        found,
    )
    return found, functions, scopes


def _ruby_walk(
    node: Node,
    source: bytes,
    path: Path,
    root: Path,
    enclosing: tuple[str, ...],
    defined: set[str],
    functions: set[str],
    scopes: set[str],
    found: list[Occurrence],
) -> None:
    if node.type == "singleton_class":
        for child in _children(node):
            _ruby_walk(child, source, path, root, enclosing, defined, functions, scopes, found)
        return
    if node.type in {"module", "class"}:
        name = _ruby_type_name(node, source)
        inner = tuple(_ruby_qualify(enclosing, name).split("::")) if name else enclosing
        for child in _children(node):
            if child.type in {"body_statement", "singleton_class"}:
                _ruby_walk(child, source, path, root, inner, defined, functions, scopes, found)
        return
    if node.type in {"method", "singleton_method"}:
        identifier = next((child for child in _children(node) if child.type == "identifier"), None)
        if identifier is not None:
            method = _text(identifier, source)
            functions.add(method)
            defined.add(method)
            if enclosing:
                parts = enclosing
                for start in range(len(parts)):
                    scopes.add(_member("::".join(parts[start:]), method))
        for child in _children(node):
            if child.type == "body_statement":
                _ruby_walk(child, source, path, root, enclosing, defined, functions, scopes, found)
        return
    if node.type == "call":
        found.extend(_ruby_call(node, source, path, root, defined))
    for child in _children(node):
        _ruby_walk(child, source, path, root, enclosing, defined, functions, scopes, found)


def _ruby_type_name(node: Node, source: bytes) -> str | None:
    for child in _children(node):
        if child.type == "constant":
            return _text(child, source)
        if child.type == "scope_resolution":
            return _text(child, source).removeprefix("::")
    return None


def _ruby_qualify(enclosing: tuple[str, ...], name: str) -> str:
    if "::" in name:
        return name
    if enclosing:
        return "::".join((*enclosing, name))
    return name


def _ruby_call(
    node: Node,
    source: bytes,
    path: Path,
    root: Path,
    defined: set[str],
) -> list[Occurrence]:
    name = _ruby_method_name(node, source)
    if name is None:
        return []
    receiver = _ruby_receiver(node, source)
    if receiver is None and name in {"require", "require_relative"}:
        required = _literal_include(node, source)
        if not required:
            return []
        if name == "require_relative" or _nearby(required, path, root):
            return []
        return [Occurrence(required, "", _line(node))]
    if receiver is None and name in defined:
        return []
    return [Occurrence(receiver or "", name, _line(node))]


def _ruby_method_name(node: Node, source: bytes) -> str | None:
    children = _children(node)
    after_dot = False
    for child in children:
        if child.type == ".":
            after_dot = True
        elif after_dot and child.type == "identifier":
            return _text(child, source)
    if after_dot:
        return None
    identifier = next((child for child in children if child.type == "identifier"), None)
    if identifier is None:
        return None
    return _text(identifier, source)


def _ruby_receiver(node: Node, source: bytes) -> str | None:
    for child in _children(node):
        if child.type == "constant":
            return _text(child, source)
        if child.type == "scope_resolution":
            return _text(child, source).removeprefix("::")
    return None


def _powershell(source: bytes, path: Path) -> tuple[list[Occurrence], set[str]]:
    tree = _parser("powershell").parse(source)
    defined: set[str] = set()
    resource = _dsc_resource_name(path)
    if resource:
        defined.add(resource.casefold())
    found: list[Occurrence] = []
    for node in _walk(tree.root_node):
        if node.type == "function_statement":
            name = next((child for child in _children(node) if child.type == "function_name"), None)
            if name is not None:
                defined.add(_text(name, source).casefold())
        elif node.type == "command":
            command = _powershell_command(node, source)
            if command is None or command.casefold() in defined:
                continue
            found.append(Occurrence("", command, _line(node)))
    return found, defined


def _dsc_resource_name(path: Path) -> str | None:
    """The name configurations use for this DSC resource, when this file defines one."""
    if path.suffix.lower() != ".psm1":
        return None
    schema = path.with_name(f"{path.stem}.schema.mof")
    if schema.is_file():
        friendly = _friendly_name(schema.read_bytes())
        if friendly:
            return friendly
    stem = path.stem
    if stem.lower().startswith("dsc_") and len(stem) > 4:
        return stem[4:]
    return None


def _friendly_name(source: bytes) -> str | None:
    marker = b'FriendlyName("'
    start = source.find(marker)
    if start < 0:
        return None
    start += len(marker)
    end = source.find(b'"', start)
    if end <= start:
        return None
    return source[start:end].decode("utf-8", "replace")


def _powershell_command(node: Node, source: bytes) -> str | None:
    """A command name, or None for a property assignment or a parse error.

    ``Dhcp = 'Enabled'`` is a DSC property, not a command. A word inside a
    comment the grammar did not recognize is dropped. A resource name such as
    ``HostsFile`` is kept here; it is internal only when this repository
    defines that resource.
    """
    if _powershell_error(node) or _powershell_assignment(node, source):
        return None
    name = next((child for child in _children(node) if child.type == "command_name"), None)
    if name is None:
        return None
    command = _text(name, source)
    if not command or "/" in command or "\\" in command:
        return None
    return _POWERSHELL_ALIASES.get(command.casefold(), command)


def _powershell_error(node: Node) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.type == "ERROR":
            return True
        parent = parent.parent
    return False


def _powershell_assignment(node: Node, source: bytes) -> bool:
    elements = next((child for child in _children(node) if child.type == "command_elements"), None)
    if elements is None:
        return False
    return any(
        child.type == "generic_token" and _text(child, source).strip() == "="
        for child in _children(elements)
    )


def _literal_include(node: Node, source: bytes) -> str:
    for child in _walk(node):
        if child.type in {"string_content", "string_fragment"}:
            return _text(child, source)
    return ""


def _nearby(name: str, path: Path, root: Path) -> bool:
    if not name or name.startswith("<"):
        return False
    relative = Path(name)
    return (path.parent / relative).exists() or (root / relative).exists() or _local(name, root) or _in_repo(name, root)


_REPO_PATHS: dict[Path, frozenset[str]] = {}
_GO_TOP_LEVEL = re.compile(
    rb"^(?:func(?:[ \t]+\([^)\n]*\))?[ \t]+|type[ \t]+|var[ \t]+|const[ \t]+)([A-Za-z_]\w*)",
    re.MULTILINE,
)
_JS_SPECIFIERS: dict[Path, tuple[frozenset[str], tuple[re.Pattern[str], ...]]] = {}


def _in_repo(name: str, root: Path) -> bool:
    """True when ``name`` is the path of a file in the repository, or a suffix of one.

    ``http.h`` matches ``lib/http.h``. ``curl/curl.h`` matches ``include/curl/curl.h``.
    """
    suffix = name.replace("\\", "/").strip("/")
    if not suffix or ".." in suffix.split("/"):
        return False
    return suffix in _repo_paths(root)


def _repo_paths(root: Path) -> frozenset[str]:
    resolved = root.resolve()
    cached = _REPO_PATHS.get(resolved)
    if cached is not None:
        return cached
    suffixes: set[str] = set()
    for directory, dirnames, filenames in os.walk(resolved):
        dirnames[:] = [name for name in dirnames if name != ".git" and name not in _SKIP_DIR_NAMES]
        for filename in filenames:
            relative = Path(directory, filename).relative_to(resolved).as_posix()
            parts = relative.split("/")
            for start in range(len(parts)):
                suffixes.add("/".join(parts[start:]))
    cached = frozenset(suffixes)
    _REPO_PATHS[resolved] = cached
    return cached


def _drop_unused_modules(found: list[Occurrence]) -> list[Occurrence]:
    used = {item.module for item in found if item.symbol}
    return [item for item in found if item.symbol or item.module not in used]


def _js_internal(module: str, root: Path) -> bool:
    """True for a relative import or a specifier this repository defines.

    A workspace package name, a ``package.json`` ``imports`` or ``exports`` key,
    and a ``tsconfig`` ``paths`` key are internal. ``vite/module-runner`` and
    ``~utils`` match. ``app/lib`` does not, unless that path is one of those keys.
    """
    if _local(module, root):
        return True
    exact, patterns = _js_specifiers(root)
    if module in exact:
        return True
    return any(pattern.fullmatch(module) for pattern in patterns)


def _js_specifiers(root: Path) -> tuple[frozenset[str], tuple[re.Pattern[str], ...]]:
    resolved = root.resolve()
    cached = _JS_SPECIFIERS.get(resolved)
    if cached is not None:
        return cached
    exact: set[str] = set()
    patterns: list[re.Pattern[str]] = []
    for directory, dirnames, filenames in os.walk(resolved):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in _SKIP_DIR_NAMES and not name.startswith(".")
        ]
        current = Path(directory)
        if "package.json" in filenames:
            _js_package_specifiers(current / "package.json", exact, patterns)
        for name in filenames:
            if name.startswith("tsconfig") and name.endswith(".json"):
                _js_tsconfig_specifiers(current / name, exact, patterns)
    cached = (frozenset(exact), tuple(patterns))
    _JS_SPECIFIERS[resolved] = cached
    return cached


def _js_package_specifiers(
    path: Path, exact: set[str], patterns: list[re.Pattern[str]]
) -> None:
    data = _read_json(path)
    if not isinstance(data, dict):
        return
    name = data.get("name")
    if isinstance(name, str) and name:
        _add_specifier(name, exact, patterns)
        exports = data.get("exports")
        if isinstance(exports, dict):
            for key in exports:
                if isinstance(key, str) and key.startswith("./"):
                    _add_specifier(f"{name}/{key[2:]}", exact, patterns)
    imports = data.get("imports")
    if isinstance(imports, dict):
        for key in imports:
            if isinstance(key, str) and key.startswith("#"):
                _add_specifier(key, exact, patterns)


def _js_tsconfig_specifiers(
    path: Path, exact: set[str], patterns: list[re.Pattern[str]]
) -> None:
    data = _read_json(path)
    if not isinstance(data, dict):
        return
    compiler = data.get("compilerOptions")
    if not isinstance(compiler, dict):
        return
    paths = compiler.get("paths")
    if not isinstance(paths, dict):
        return
    for key in paths:
        if isinstance(key, str) and key:
            _add_specifier(key, exact, patterns)


def _add_specifier(value: str, exact: set[str], patterns: list[re.Pattern[str]]) -> None:
    if "*" not in value:
        exact.add(value)
        return
    body = ".*".join(re.escape(part) for part in value.split("*"))
    patterns.append(re.compile(f"^{body}$"))


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _local(module: str, root: Path) -> bool:
    if module.startswith("."):
        return True
    head = module.split("/", 1)[0].split(".", 1)[0]
    if not head:
        return True
    return any(
        candidate.exists()
        for candidate in (
            root / head,
            root / f"{head}.py",
            root / "src" / head,
            root / "src" / f"{head}.py",
        )
    )


def _parser(language: str) -> Parser:
    parser = _parsers.get(language)
    if parser is None:
        parser = get_parser(language)
        _parsers[language] = parser
    return parser


def _children(node: Node) -> list[Node]:
    """Copy children before walking them.

    Iterating ``node.children`` uses one cursor for the whole tree. A nested
    iteration segfaults once the tree is large enough.
    """
    return [node.child(index) for index in range(node.child_count)]


def _walk(node: Node):
    """Yield nodes in preorder.

    An explicit stack keeps deeply nested expressions, such as a stress-test
    chain of binary operators, from exhausting the Python call stack.
    """
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(_children(current)))


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8")


def _line(node: Node) -> int:
    # Index the point. tree-sitter 0.26.0's Point.row getter drops a reference
    # and segfaults once the line number is no longer a small interned int.
    return int(node.start_point[0]) + 1
