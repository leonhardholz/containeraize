"""Label symbols that are known to perform I/O."""

from __future__ import annotations

import re
import sys
from collections import deque

from findio.ai import BATCH_SIZE, PROMPT_VERSION, AIClassifier, AIUnavailable, parse_response, response_problem
from findio.cache import ClassificationCache
from findio.report import Label, Symbol

_KIND_ORDER = ("filesystem", "network", "service", "process", "database")

# (language, module, symbol) -> kinds. An empty symbol is the whole module.
_IO: dict[tuple[str, str, str], tuple[str, ...]] = {}


def _add(language: str, module: str, symbol: str, *kinds: str) -> None:
    ordered = tuple(kind for kind in _KIND_ORDER if kind in kinds)
    _IO[(language, module, symbol)] = ordered


def _functions(language: str, module: str, names: tuple[str, ...], kind: str) -> None:
    for name in names:
        _add(language, module, name, kind)


_add("python", "", "open", "filesystem")
_add("python", "", "input", "filesystem")
_functions(
    "python",
    "os",
    (
        "open",
        "read",
        "write",
        "remove",
        "unlink",
        "rename",
        "replace",
        "mkdir",
        "makedirs",
        "rmdir",
        "listdir",
        "scandir",
        "walk",
        "chdir",
        "chmod",
        "stat",
        "lstat",
        "symlink",
        "truncate",
    ),
    "filesystem",
)
_functions("python", "os", ("system", "popen"), "process")
_functions(
    "python",
    "os.path",
    ("exists", "lexists", "isfile", "isdir", "getsize", "getmtime"),
    "filesystem",
)
_functions("python", "os", ("getcwd", "readlink", "link", "removedirs"), "filesystem")
_add("python", "io", "open", "filesystem")
_add("python", "codecs", "open", "filesystem")
_functions(
    "python",
    "tempfile",
    ("TemporaryFile", "NamedTemporaryFile", "SpooledTemporaryFile", "mkstemp", "mkdtemp"),
    "filesystem",
)
_add("python", "tempfile", "", "filesystem")
_add("python", "gzip", "open", "filesystem")
_add("python", "bz2", "open", "filesystem")
_add("python", "lzma", "open", "filesystem")
_add("python", "zipfile", "ZipFile", "filesystem")
_add("python", "tarfile", "open", "filesystem")
_functions("python", "glob", ("glob", "iglob"), "filesystem")
_functions("python", "shutil", ("copytree", "unpack_archive", "make_archive", "chown"), "filesystem")
_add("python", "urllib3", "", "network")
for _module in ("ftplib", "smtplib", "poplib", "imaplib"):
    _add("python", _module, "", "network")
_functions(
    "python",
    "subprocess",
    ("run", "call", "check_call", "check_output", "Popen", "getoutput", "getstatusoutput"),
    "process",
)
_add("python", "subprocess", "", "process")
_functions(
    "python",
    "requests",
    ("get", "post", "put", "patch", "delete", "head", "request", "Session"),
    "network",
)
_add("python", "requests", "", "network")
for _module in ("boto3", "botocore", "google.cloud.storage", "azure.storage.blob", "openai", "litellm", "ldap", "ldap3"):
    _add("python", _module, "", "service")
_add("python", "socket", "", "network")
_add("python", "http.client", "", "network")
_functions("python", "urllib.request", ("urlopen", "urlretrieve"), "network")
_add("python", "sqlite3", "connect", "database")
_add("python", "sqlite3", "", "database")
for _module in ("psycopg", "psycopg2", "pymysql", "redis", "pymongo"):
    _add("python", _module, "", "database")
_functions(
    "python",
    "shutil",
    ("copy", "copy2", "copyfile", "move", "rmtree", "disk_usage"),
    "filesystem",
)
_functions(
    "python",
    "pathlib",
    ("Path", "read_text", "read_bytes", "write_text", "write_bytes", "unlink", "mkdir", "open"),
    "filesystem",
)

for _language in ("javascript", "typescript", "tsx"):
    _add(_language, "", "fetch", "network")
    _add(_language, "", "XMLHttpRequest", "network")
    for _module in ("fs", "node:fs"):
        _functions(
            _language,
            _module,
            (
                "readFile",
                "readFileSync",
                "writeFile",
                "writeFileSync",
                "appendFile",
                "appendFileSync",
                "mkdir",
                "mkdirSync",
                "rm",
                "rmSync",
                "unlink",
                "unlinkSync",
                "stat",
                "statSync",
                "readdir",
                "readdirSync",
                "createReadStream",
                "createWriteStream",
                "existsSync",
                "access",
                "accessSync",
                "copyFile",
                "copyFileSync",
                "cp",
                "cpSync",
                "rename",
                "renameSync",
                "realpath",
                "realpathSync",
                "lstat",
                "lstatSync",
                "truncate",
                "truncateSync",
                "open",
                "openSync",
            ),
            "filesystem",
        )
        _add(_language, _module, "", "filesystem")
    for _module in ("fs/promises", "node:fs/promises"):
        _functions(
            _language,
            _module,
            (
                "readFile",
                "writeFile",
                "appendFile",
                "mkdir",
                "rm",
                "unlink",
                "stat",
                "lstat",
                "readdir",
                "copyFile",
                "rename",
                "access",
                "open",
                "cp",
            ),
            "filesystem",
        )
        _add(_language, _module, "", "filesystem")
    for _module in ("net", "http", "https", "dgram", "node:net", "node:http", "node:https"):
        _add(_language, _module, "", "network")
    for _module in ("child_process", "node:child_process"):
        _functions(
            _language,
            _module,
            ("exec", "execSync", "execFile", "execFileSync", "spawn", "spawnSync", "fork"),
            "process",
        )
        _add(_language, _module, "", "process")

_functions(
    "go",
    "os",
    (
        "ReadFile",
        "WriteFile",
        "Open",
        "OpenFile",
        "Create",
        "Remove",
        "RemoveAll",
        "Mkdir",
        "MkdirAll",
        "Rename",
        "Stat",
        "Chmod",
        "ReadDir",
        "CreateTemp",
        "MkdirTemp",
        "Lstat",
        "Symlink",
        "Truncate",
        "Getwd",
        "Chdir",
        "Readlink",
    ),
    "filesystem",
)
_add("go", "os", "", "filesystem")
_functions("go", "io", ("ReadAll", "Copy", "CopyBuffer"), "filesystem")
_functions(
    "go",
    "io/ioutil",
    ("ReadFile", "WriteFile", "ReadDir", "ReadAll", "TempDir", "TempFile"),
    "filesystem",
)
_functions("go", "path/filepath", ("Walk", "WalkDir", "Glob"), "filesystem")
_functions("go", "net", ("Dial", "Listen", "ListenPacket"), "network")
_add("go", "net", "", "network")
_functions("go", "net/http", ("Get", "Post", "Head", "NewRequest", "PostForm", "ListenAndServe"), "network")
_add("go", "net/http", "", "network")
for _module in ("gocloud.dev/blob/s3blob", "gocloud.dev/blob/gcsblob", "gocloud.dev/blob/azureblob"):
    _add("go", _module, "", "service")
_functions("go", "os/exec", ("Command", "CommandContext"), "process")
_add("go", "os/exec", "", "process")
_add("go", "database/sql", "Open", "database")
_add("go", "database/sql", "", "database")

_add("rust", "std::fs", "", "filesystem")
_functions(
    "rust",
    "std::fs",
    (
        "read",
        "read_to_string",
        "write",
        "copy",
        "rename",
        "remove_file",
        "remove_dir",
        "remove_dir_all",
        "create_dir",
        "create_dir_all",
        "read_dir",
        "metadata",
        "canonicalize",
        "File",
        "OpenOptions",
    ),
    "filesystem",
)
_add("rust", "std::io", "copy", "filesystem")
_add("rust", "std::net", "", "network")
_add("rust", "std::process", "", "process")

for _module in (
    "java.io",
    "java.io.File",
    "java.io.FileInputStream",
    "java.io.FileOutputStream",
    "java.io.FileReader",
    "java.io.FileWriter",
    "java.io.RandomAccessFile",
    "java.nio.file",
    "java.nio.file.Files",
    "java.nio.file.Path",
    "java.nio.file.Paths",
):
    _add("java", _module, "", "filesystem")
    _add("kotlin", _module, "", "filesystem")
_add("kotlin", "kotlin.io", "", "filesystem")
for _module in (
    "java.net",
    "java.net.Socket",
    "java.net.ServerSocket",
    "java.net.URL",
    "java.net.URI",
    "java.net.HttpURLConnection",
    "java.net.http",
    "java.net.http.HttpClient",
    "java.net.http.HttpRequest",
):
    _add("java", _module, "", "network")
    _add("kotlin", _module, "", "network")
for _module in ("java.sql", "java.sql.DriverManager", "java.sql.Connection"):
    _add("java", _module, "", "database")
    _add("kotlin", _module, "", "database")

_functions(
    "php",
    "",
    (
        "file_get_contents",
        "file_put_contents",
        "fopen",
        "fclose",
        "fread",
        "fwrite",
        "file",
        "file_exists",
        "is_file",
        "is_dir",
        "copy",
        "rename",
        "scandir",
        "opendir",
        "readfile",
        "unlink",
        "mkdir",
        "rmdir",
        "chmod",
        "touch",
        "tempnam",
        "tmpfile",
    ),
    "filesystem",
)
_functions(
    "php",
    "",
    ("curl_exec", "curl_init", "curl_setopt", "fsockopen", "stream_socket_client"),
    "network",
)
_functions("php", "", ("exec", "shell_exec", "system", "passthru", "proc_open"), "process")
_functions("php", "", ("mysqli_connect", "pg_connect"), "database")

for _language in ("c", "cpp"):
    _functions(
        _language,
        "",
        (
            "fopen",
            "fdopen",
            "fclose",
            "fread",
            "fwrite",
            "fgets",
            "fputs",
            "getline",
            "open",
            "openat",
            "read",
            "write",
            "unlink",
            "remove",
            "rename",
            "stat",
            "lstat",
            "fstat",
            "access",
            "chmod",
            "mkdir",
            "rmdir",
            "opendir",
            "readdir",
            "chdir",
            "getcwd",
        ),
        "filesystem",
    )
    for _header in ("stdio.h", "fcntl.h", "dirent.h", "sys/stat.h", "fstream", "filesystem"):
        _add(_language, _header, "", "filesystem")
    _functions(
        _language,
        "",
        ("socket", "connect", "send", "recv", "sendto", "recvfrom", "accept", "listen", "bind", "getaddrinfo"),
        "network",
    )
    for _header in ("sys/socket.h", "netdb.h", "arpa/inet.h"):
        _add(_language, _header, "", "network")
    _functions(_language, "", ("system", "popen", "execve", "execvp", "fork"), "process")
    _add(_language, "", "sqlite3_open", "database")

_functions("ruby", "", ("system", "exec", "spawn"), "process")
_add("ruby", "", "open", "filesystem")
_add("ruby", "File", "", "filesystem")
_add("ruby", "IO", "popen", "process")
_functions("ruby", "IO", ("read", "binread", "write", "binwrite", "foreach"), "filesystem")
for _module in ("open-uri", "net/http", "socket"):
    _add("ruby", _module, "", "network")

_functions(
    "powershell",
    "",
    (
        "Get-Content",
        "Set-Content",
        "Out-File",
        "Add-Content",
        "Get-ChildItem",
        "New-Item",
        "Remove-Item",
        "Copy-Item",
        "Move-Item",
        "Rename-Item",
        "Test-Path",
        "Clear-Content",
    ),
    "filesystem",
)
_functions("powershell", "", ("Invoke-WebRequest", "Invoke-RestMethod"), "network")
_functions("powershell", "", ("Invoke-Command", "Start-Process"), "process")
# Local Windows network-stack cmdlets. A name match keeps them `network`
# even when a model has called the same command a remote service.
_WINDOWS_NETWORK = re.compile(
    r"(netadapter|netip|netroute|netfirewall|netconnection|netlbfo|netbios|dnsclient)",
    re.IGNORECASE,
)
_CIM = frozenset({"get-ciminstance", "get-cimassociatedinstance", "invoke-cimmethod"})
# Console and build-harness commands. They are known not to be I/O, so the
# model is not asked.
_POWERSHELL_NOT_IO = frozenset(
    {
        "write-host",
        "write-verbose",
        "write-debug",
        "invoke-build",
        "set-buildheader",
        "write-build",
    }
)

for _name in ("curl", "wget", "ssh", "scp", "nc", "ncat", "socat", "rsync"):
    _add("shell", "", _name, "network")
for _name in (
    "rm",
    "cp",
    "mv",
    "mkdir",
    "rmdir",
    "touch",
    "cat",
    "chmod",
    "chown",
    "ln",
    "dd",
    "tar",
    "find",
    "ls",
    "mktemp",
    "gzip",
    "gunzip",
    "zcat",
    "bzip2",
    "xz",
    "zip",
    "unzip",
):
    _add("shell", "", _name, "filesystem")
_add("shell", "", "redirect", "filesystem")

for _module in (
    "File",
    "Directory",
    "FileInfo",
    "DirectoryInfo",
    "FileStream",
    "StreamReader",
    "StreamWriter",
    "System.IO",
    "System.IO.File",
    "System.IO.Directory",
    "System.IO.FileInfo",
    "System.IO.DirectoryInfo",
    "System.IO.FileStream",
    "System.IO.StreamReader",
    "System.IO.StreamWriter",
):
    _add("csharp", _module, "", "filesystem")
for _module in (
    "HttpClient",
    "WebClient",
    "Socket",
    "TcpClient",
    "UdpClient",
    "System.Net.Http",
    "System.Net.Http.HttpClient",
    "System.Net.Sockets",
    "System.Net.Sockets.Socket",
    "System.Net.WebClient",
):
    _add("csharp", _module, "", "network")
for _module in ("Process", "System.Diagnostics.Process"):
    _add("csharp", _module, "", "process")
for _module in (
    "SqlConnection",
    "SqlCommand",
    "System.Data.SqlClient",
    "Microsoft.Data.SqlClient",
    "SqliteConnection",
):
    _add("csharp", _module, "", "database")

# Modules entered only as a whole (empty symbol). A call on one of these is
# the module's kind. Modules that also list individual functions stay exact,
# so os.path.join is not treated as os.
_WHOLE_MODULES = {
    (language, module)
    for language, module, symbol in _IO
    if symbol == ""
    and not any(
        other_symbol
        for other_language, other_module, other_symbol in _IO
        if other_symbol and other_language == language and other_module == module
    )
}
_POWERSHELL = {
    (module.casefold(), symbol.casefold()): kinds
    for (language, module, symbol), kinds in _IO.items()
    if language == "powershell"
}


def _windows_network(symbol: str) -> bool:
    folded = symbol.casefold()
    return folded in _CIM or _WINDOWS_NETWORK.search(folded) is not None


def _kinds(language: str, module: str, symbol: str) -> tuple[str, ...] | None:
    if language == "powershell":
        found = _POWERSHELL.get((module.casefold(), symbol.casefold()))
        if found is not None:
            return found
        if _windows_network(symbol):
            return ("network",)
    seen: set[tuple[str, str]] = set()
    while (module, symbol) not in seen:
        seen.add((module, symbol))
        kinds = _IO.get((language, module, symbol))
        if kinds is not None:
            return kinds
        if "::" in module:
            parent, _, name = module.rpartition("::")
            type_kinds = _IO.get((language, parent, name))
            if type_kinds is not None:
                return type_kinds
            if (language, parent) in _WHOLE_MODULES:
                return _IO[(language, parent, "")]
            module = parent
            symbol = f"{name}.{symbol}" if symbol else name
            continue
        if "." not in symbol:
            if symbol and (language, module) in _WHOLE_MODULES:
                return _IO[(language, module, "")]
            return None
        head, _, tail = symbol.partition(".")
        shifted = _IO.get((language, f"{module}.{head}" if module else head, tail))
        if shifted is not None:
            return shifted
        kinds = _IO.get((language, module, symbol.rsplit(".", 1)[-1]))
        if kinds is not None:
            return kinds
        module = f"{module}.{head}" if module else head
        symbol = tail
    return None


class StaticClassifier:
    """Classify symbols from a fixed table. Unknown symbols are not I/O."""

    def lookup(self, symbol: Symbol) -> Label | None:
        """The table's label, or None when the symbol is not in the table."""
        if (
            symbol.language == "powershell"
            and symbol.symbol.casefold() in _POWERSHELL_NOT_IO
        ):
            return Label(io=False, kinds=())
        kinds = _kinds(symbol.language, symbol.module, symbol.symbol)
        if kinds is None:
            return None
        return Label(io=True, kinds=kinds)

    def classify(self, symbols: list[Symbol]) -> list[Label]:
        return [_known(self.lookup(symbol)) for symbol in symbols]


class CascadingClassifier:
    """Static table, then the cache, then one LiteLLM batch at a time."""

    def __init__(self, cache: ClassificationCache, ai: AIClassifier, *, refresh: bool = False) -> None:
        self.cache = cache
        self.ai = ai
        self.refresh = refresh
        self._static = StaticClassifier()
        self._ai_enabled = True

    def classify(self, symbols: list[Symbol]) -> list[Label]:
        labels: dict[Symbol, Label] = {}
        unknown: list[Symbol] = []
        pending: set[Symbol] = set()
        for symbol in symbols:
            known = self._static.lookup(symbol)
            if known is not None:
                labels[symbol] = known
                continue
            if symbol in pending:
                continue
            if not self.refresh:
                cached = self.cache.get(symbol, model=self.ai.model, prompt_version=PROMPT_VERSION)
                if cached is not None:
                    labels[symbol] = cached
                    continue
            pending.add(symbol)
            unknown.append(symbol)
        if unknown and self._ai_enabled:
            self._ask(unknown, labels)
        return [_known(labels.get(symbol)) for symbol in symbols]

    def _ask(self, unknown: list[Symbol], labels: dict[Symbol, Label]) -> None:
        pending: deque[list[Symbol]] = deque(_batches(unknown))
        while pending:
            batch = pending.popleft()
            print(_classify_line(batch), file=sys.stderr, flush=True)
            try:
                text = self.ai.ask(batch)
            except AIUnavailable as exc:
                self._ai_enabled = False
                print(
                    f"findio: LiteLLM is unavailable ({exc}); remaining symbols keep the static result",
                    file=sys.stderr,
                    flush=True,
                )
                return
            parsed = parse_response(text, batch)
            if parsed is None:
                problem = response_problem(text, batch)
                if len(batch) == 1:
                    print(
                        f"findio: classification failed for {_symbol_name(batch[0])} ({problem}); leaving it as not I/O",
                        file=sys.stderr,
                        flush=True,
                    )
                    continue
                print(
                    f"findio: classification reply could not be used ({problem}); splitting {len(batch)} {batch[0].language} symbols",
                    file=sys.stderr,
                    flush=True,
                )
                middle = len(batch) // 2
                pending.appendleft(batch[middle:])
                pending.appendleft(batch[:middle])
                continue
            for symbol in batch:
                label = parsed[(symbol.module, symbol.symbol)]
                labels[symbol] = label
                self.cache.put(symbol, label, model=self.ai.model, prompt_version=PROMPT_VERSION)


def _classify_line(batch: list[Symbol]) -> str:
    names = " ".join(_symbol_name(symbol) for symbol in batch)
    return f"classify {batch[0].language} {len(batch)} {names}"


def _symbol_name(symbol: Symbol) -> str:
    if symbol.module and symbol.symbol:
        return f"{symbol.module}.{symbol.symbol}"
    return symbol.symbol or symbol.module


def _known(label: Label | None) -> Label:
    return label if label is not None else Label(io=False, kinds=())


def _batches(symbols: list[Symbol]) -> list[list[Symbol]]:
    grouped: dict[str, list[Symbol]] = {}
    for symbol in symbols:
        grouped.setdefault(symbol.language, []).append(symbol)
    batches: list[list[Symbol]] = []
    for language in sorted(grouped):
        ordered = sorted(grouped[language], key=lambda symbol: (symbol.module, symbol.symbol))
        for start in range(0, len(ordered), BATCH_SIZE):
            batches.append(ordered[start : start + BATCH_SIZE])
    return batches
