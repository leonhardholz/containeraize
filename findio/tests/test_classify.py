"""Static classification and the report it produces."""

from __future__ import annotations

from pathlib import Path

import pytest

from findio import Label, Location, Report, Symbol, SymbolGroup, scan
from findio.classify import StaticClassifier
from findio.cli import available_cores, main


def test_scan_many_go_selectors(tmp_path: Path) -> None:
    body = "\n".join(f'\tos.Remove("f{index}")' for index in range(40))
    (tmp_path / "many.go").write_text(
        f'package main\nimport "os"\nfunc main() {{\n{body}\n}}\n',
        encoding="utf-8",
    )

    report = scan(tmp_path, cache=None)

    assert len(report.symbols) == 1
    group = report.symbols[0]
    assert (group.module, group.symbol) == ("os", "Remove")
    assert len(group.locations[0].lines) == 40


def test_scan_javascript_deeper_than_the_call_stack(tmp_path: Path) -> None:
    nested = "1"
    for _ in range(1200):
        nested = f"({nested})"
    (tmp_path / "deep.js").write_text(f"fetch({nested})\n", encoding="utf-8")

    report = scan(tmp_path, cache=None)

    assert [(group.module, group.symbol) for group in report.symbols] == [("", "fetch")]


def test_scan_go_call_past_line_256(tmp_path: Path) -> None:
    padding = "\n" * 300
    (tmp_path / "late.go").write_text(
        f'package main\nimport "os"\nfunc main() {{{padding}\tos.Remove("a")\n}}\n',
        encoding="utf-8",
    )

    report = scan(tmp_path, cache=None)

    group = report.symbols[0]
    assert (group.module, group.symbol) == ("os", "Remove")
    assert group.locations[0].lines[0] > 256


def test_static_classifier_labels_known_symbols() -> None:
    symbols = [
        Symbol("python", "requests", "get"),
        Symbol("python", "os", "path.exists"),
        Symbol("python", "json", "loads"),
        Symbol("php", "", "file_get_contents"),
        Symbol("shell", "", "curl"),
        Symbol("rust", "std::fs", "read"),
        Symbol("java", "java.sql", ""),
    ]
    labels = StaticClassifier().classify(symbols)

    assert [(label.io, label.kinds) for label in labels] == [
        (True, ("network",)),
        (True, ("filesystem",)),
        (False, ()),
        (True, ("filesystem",)),
        (True, ("network",)),
        (True, ("filesystem",)),
        (True, ("database",)),
    ]


def test_scan_reports_known_io(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "client.py").write_text(
        "import requests\n"
        "from mypkg import helper\n"
        "\n"
        "def main():\n"
        "    open('a')\n"
        "    requests.get('http://example.com')\n"
        "    helper()\n",
        encoding="utf-8",
    )
    (source / "mypkg.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (tmp_path / "fetch.sh").write_text("curl -fsSL https://example.com > out.txt\n", encoding="utf-8")
    (tmp_path / "main.go").write_text(
        'package main\nimport "os"\nfunc main() { os.Remove("a") }\n',
        encoding="utf-8",
    )
    (tmp_path / "app.js").write_text(
        "import { readFileSync } from 'fs'\nfetch('http://example.com')\nreadFileSync('a')\n",
        encoding="utf-8",
    )
    (tmp_path / "pure.py").write_text("import json\njson.loads('{}')\n", encoding="utf-8")

    report = scan(tmp_path, cache=None)
    groups = {(item.language, item.module, item.symbol): item for item in report.symbols}

    assert set(groups) == {
        ("python", "", "open"),
        ("python", "requests", "get"),
        ("shell", "", "curl"),
        ("shell", "", "redirect"),
        ("go", "os", "Remove"),
        ("javascript", "", "fetch"),
        ("javascript", "fs", "readFileSync"),
    }
    open_hit = groups[("python", "", "open")]
    assert open_hit.kinds == ("filesystem",)
    assert [(item.path, item.lines) for item in open_hit.locations] == [("src/client.py", (5,))]
    assert [(item.path, item.lines) for item in groups[("python", "requests", "get")].locations] == [
        ("src/client.py", (6,))
    ]
    assert groups[("shell", "", "curl")].kinds == ("network",)
    assert groups[("shell", "", "redirect")].kinds == ("filesystem",)
    assert groups[("go", "os", "Remove")].kinds == ("filesystem",)
    assert groups[("javascript", "", "fetch")].kinds == ("network",)
    assert groups[("javascript", "fs", "readFileSync")].kinds == ("filesystem",)
    assert [item.kinds[0] for item in report.symbols] == [
        "filesystem",
        "filesystem",
        "filesystem",
        "filesystem",
        "network",
        "network",
        "network",
    ]
    assert scan(tmp_path, cache=None, jobs=2) == report


def test_dotted_os_import_keeps_a_single_path_prefix(tmp_path: Path) -> None:
    (tmp_path / "paths.py").write_text(
        "import os.path\n\ndef main():\n    os.path.isdir('a')\n",
        encoding="utf-8",
    )

    report = scan(tmp_path, cache=None)

    assert [(group.module, group.symbol) for group in report.symbols] == [("os", "path.isdir")]


def test_service_client_is_not_generic_network(tmp_path: Path) -> None:
    (tmp_path / "store.py").write_text(
        "import boto3\n"
        "import requests\n"
        "boto3.client('s3')\n"
        "requests.get('https://example.com')\n",
        encoding="utf-8",
    )
    (tmp_path / "bucket.go").write_text(
        'package main\nimport "gocloud.dev/blob/s3blob"\nimport "net/http"\n'
        "func main() { s3blob.OpenBucket(nil, nil); http.Get(\"https://example.com\") }\n",
        encoding="utf-8",
    )

    report = scan(tmp_path, cache=None)
    groups = {(item.language, item.module, item.symbol): item.kinds for item in report.symbols}

    assert groups[("python", "boto3", "client")] == ("service",)
    assert groups[("python", "requests", "get")] == ("network",)
    assert groups[("go", "gocloud.dev/blob/s3blob", "OpenBucket")] == ("service",)
    assert groups[("go", "net/http", "Get")] == ("network",)


def test_windows_network_cmdlets_are_not_a_service(tmp_path: Path) -> None:
    (tmp_path / "net.ps1").write_text(
        "Get-NetAdapter\nNew-NetIPAddress\nGet-CimInstance\nSet-NetIPAddress\nWrite-Host 'ok'\n",
        encoding="utf-8",
    )

    report = scan(tmp_path, cache=None)
    groups = {(item.language, item.module, item.symbol): item.kinds for item in report.symbols}

    assert groups[("powershell", "", "Get-NetAdapter")] == ("network",)
    assert groups[("powershell", "", "New-NetIPAddress")] == ("network",)
    assert groups[("powershell", "", "Get-CimInstance")] == ("network",)
    assert groups[("powershell", "", "Set-NetIPAddress")] == ("network",)
    assert ("powershell", "", "Write-Host") not in groups


def test_dsc_properties_are_not_commands(tmp_path: Path) -> None:
    (tmp_path / "host.ps1").write_text(
        "<#\n"
        ".PARAMETER DnsSecEnable\n"
        "Enables Domain Name System Security Extensions (DNSSEC) on the rule.\n"
        "#>\n"
        "Configuration Example {\n"
        "    Node localhost {\n"
        "        HostsFile AddEntry {\n"
        "            HostName = 'host'\n"
        "            Dhcp = 'Enabled'\n"
        "        }\n"
        "    }\n"
        "}\n"
        "Get-NetAdapter\n"
        "Get-Content a\n",
        encoding="utf-8",
    )

    report = scan(tmp_path, cache=None)
    found = {group.symbol for group in report.symbols}

    assert "Get-NetAdapter" in found
    assert "Get-Content" in found
    assert "DNSSEC" not in found
    assert "Dhcp" not in found
    assert "HostName" not in found


def test_dsc_resource_defined_in_the_repo_is_internal(tmp_path: Path) -> None:
    class Record:
        def __init__(self) -> None:
            self.seen: list[Symbol] = []

        def classify(self, symbols: list[Symbol]) -> list[Label]:
            self.seen.extend(symbols)
            return [Label(io=False, kinds=()) for _ in symbols]

    resource = tmp_path / "DSC_HostsFile"
    resource.mkdir()
    (resource / "DSC_HostsFile.schema.mof").write_text(
        '[ClassVersion("1.0.0"), FriendlyName("HostsFile")]\nclass DSC_HostsFile {}\n',
        encoding="utf-8",
    )
    (resource / "DSC_HostsFile.psm1").write_text(
        "function Get-TargetResource { Get-Content a }\n",
        encoding="utf-8",
    )
    (tmp_path / "next.ps1").write_text(
        "HostsFile AddEntry\n"
        "{\n"
        "    HostName = 'host'\n"
        "}\n"
        "File Setup\n"
        "{\n"
        "    DestinationPath = 'C:\\a'\n"
        "}\n"
        "Get-NetAdapter\n",
        encoding="utf-8",
    )
    record = Record()

    scan(tmp_path, client=record)
    seen = {symbol.symbol for symbol in record.seen}

    assert "HostsFile" not in seen
    assert "File" in seen
    assert "Get-Content" in seen
    assert "Get-NetAdapter" in seen
    assert "HostName" not in seen


def test_path_join_is_not_io(tmp_path: Path) -> None:
    (tmp_path / "join.py").write_text("import os\nos.path.join('a', 'b')\n", encoding="utf-8")

    assert scan(tmp_path, cache=None).symbols == ()


def test_scan_covers_stdlib_and_more_languages(tmp_path: Path) -> None:
    (tmp_path / "net.py").write_text(
        "import codecs\nimport urllib3\ncodecs.open('a')\nurllib3.PoolManager()\n",
        encoding="utf-8",
    )
    (tmp_path / "read.go").write_text(
        'package main\nimport "io"\nfunc main() { io.ReadAll(nil) }\n',
        encoding="utf-8",
    )
    (tmp_path / "disk.js").write_text(
        "import fs from 'fs'\nfs.existsSync('a')\nfs.promises.readFile('a')\n",
        encoding="utf-8",
    )
    (tmp_path / "tool.sh").write_text("mktemp\ngzip a\n", encoding="utf-8")
    (tmp_path / "main.c").write_text(
        '#include <stdio.h>\nstatic void helper(void) {}\nint main(void) { fopen("a", "r"); helper(); }\n',
        encoding="utf-8",
    )
    (tmp_path / "Main.java").write_text(
        "import java.nio.file.Files;\n"
        "class Main { void m() throws Exception { Files.readAllBytes(null); } }\n",
        encoding="utf-8",
    )
    (tmp_path / "Main.kt").write_text(
        "import java.nio.file.Files\nfun main() { Files.readAllBytes(null) }\n",
        encoding="utf-8",
    )
    (tmp_path / "lib.rs").write_text(
        'use std::fs::read;\nfn main() { let _ = read("a"); }\n',
        encoding="utf-8",
    )
    (tmp_path / "page.php").write_text(
        "<?php\nfunction helper() {}\nfile_get_contents('a');\nhelper();\n",
        encoding="utf-8",
    )
    (tmp_path / "app.rb").write_text(
        "def helper; end\nFile.read('a')\nsystem('ls')\nhelper()\n",
        encoding="utf-8",
    )
    (tmp_path / "read.ps1").write_text(
        "function Helper {}\nGet-Content a\nHelper\n",
        encoding="utf-8",
    )
    (tmp_path / "Program.cs").write_text(
        "using System.IO;\n"
        "using Sys = System.Net.Http;\n"
        "class Program {\n"
        "  static void Main() {\n"
        "    Directory.Delete(\"a\");\n"
        "    file.Read();\n"
        "    System.IO.File.WriteAllText(\"b\", \"c\");\n"
        "    Process.Start(\"x\");\n"
        "    WriteLine(\"hi\");\n"
        "    new Sys.HttpClient();\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )

    report = scan(tmp_path, cache=None)
    found = {(group.language, group.module, group.symbol) for group in report.symbols}

    assert ("python", "codecs", "open") in found
    assert ("python", "urllib3", "PoolManager") in found
    assert ("go", "io", "ReadAll") in found
    assert ("javascript", "fs", "existsSync") in found
    assert ("javascript", "fs", "promises.readFile") in found
    assert ("shell", "", "mktemp") in found
    assert ("shell", "", "gzip") in found
    assert ("c", "stdio.h", "") in found
    assert ("c", "", "fopen") in found
    assert ("c", "", "helper") not in found
    assert ("java", "java.nio.file.Files", "readAllBytes") in found
    assert ("kotlin", "java.nio.file.Files", "readAllBytes") in found
    assert ("rust", "std::fs", "read") in found
    assert ("php", "", "file_get_contents") in found
    assert ("php", "", "helper") not in found
    assert ("ruby", "File", "read") in found
    assert ("ruby", "", "system") in found
    assert ("powershell", "", "Get-Content") in found
    assert ("powershell", "", "Helper") not in found
    assert ("csharp", "Directory", "Delete") in found
    assert ("csharp", "System.IO.File", "WriteAllText") in found
    assert ("csharp", "Process", "Start") in found
    assert ("csharp", "System.Net.Http.HttpClient", "") in found
    assert ("csharp", "", "WriteLine") not in found
    assert ("csharp", "file", "Read") not in found


def test_ruby_methods_defined_in_the_repo_are_not_classified(tmp_path: Path) -> None:
    class Record:
        def __init__(self) -> None:
            self.seen: list[Symbol] = []

        def classify(self, symbols: list[Symbol]) -> list[Label]:
            self.seen.extend(symbols)
            return [Label(io=False, kinds=()) for _ in symbols]

    (tmp_path / "site.rb").write_text(
        "module App\n"
        "  class Site\n"
        "    def read; end\n"
        "    def exist?; end\n"
        "    def self.configuration; end\n"
        "  end\n"
        "  module Utils\n"
        "    def self.safe_glob; end\n"
        "    module Internet\n"
        "      def self.connected?; end\n"
        "    end\n"
        "  end\n"
        "end\n",
        encoding="utf-8",
    )
    (tmp_path / "use.rb").write_text(
        "module App\n"
        "  def run(doc, stdout)\n"
        "    doc.read\n"
        "    exist?\n"
        "    Site.read\n"
        "    Site.new\n"
        "    Site.configuration\n"
        "    Utils.safe_glob(1)\n"
        "    App::Utils.safe_glob(1)\n"
        "    Utils::Internet.connected?\n"
        "    File.read('a')\n"
        "    stdout.read\n"
        "    system('ls')\n"
        "  end\n"
        "end\n",
        encoding="utf-8",
    )

    client = Record()
    scan(tmp_path, client=client)
    seen = {(item.language, item.module, item.symbol) for item in client.seen}

    assert ("ruby", "File", "read") in seen
    assert ("ruby", "", "system") in seen
    assert ("ruby", "Site", "new") in seen
    assert ("ruby", "", "read") not in seen
    assert ("ruby", "", "exist?") not in seen
    assert ("ruby", "Site", "read") not in seen
    assert ("ruby", "Site", "configuration") not in seen
    assert ("ruby", "Utils", "safe_glob") not in seen
    assert ("ruby", "App::Utils", "safe_glob") not in seen
    assert ("ruby", "Utils::Internet", "connected?") not in seen


def test_command_skips_a_shell_function(tmp_path: Path) -> None:
    (tmp_path / "shadow.sh").write_text("curl() { :; }\nwget() { :; }\n", encoding="utf-8")
    (tmp_path / "use.sh").write_text(
        ". ./shadow.sh\n"
        "curl\n"
        "sudo wget\n"
        "command curl -V\n"
        "W=curl\n"
        "download() {\n"
        "  local NVM_DOWNLOADER\n"
        "  NVM_DOWNLOADER=''\n"
        "  NVM_DOWNLOADER='curl'\n"
        "  NVM_DOWNLOADER='wget'\n"
        '  command "${NVM_DOWNLOADER}" "$@"\n'
        "}\n"
        "other() {\n"
        "  V=$(printf x)\n"
        '  command "${V}"\n'
        "}\n"
        "outside() {\n"
        '  command "${W}"\n'
        "}\n",
        encoding="utf-8",
    )

    lines: dict[str, tuple[int, ...]] = {}
    for group in scan(tmp_path, cache=None).symbols:
        for location in group.locations:
            if location.path == "use.sh":
                lines[group.symbol] = location.lines

    assert lines["curl"] == (4, 11)
    assert lines["wget"] == (11,)


def test_shell_function_hides_a_call_only_along_source(tmp_path: Path) -> None:
    (tmp_path / "lib.sh").write_text("curl() { :; }\ncurl\n", encoding="utf-8")
    (tmp_path / "app.sh").write_text("curl\n", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "mid.sh").write_text('. ../lib.sh\n', encoding="utf-8")
    (nested / "top.sh").write_text(". ./mid.sh\ncurl\n", encoding="utf-8")
    (tmp_path / "dyn.sh").write_text('. "$LIB"\ncurl\n', encoding="utf-8")
    (tmp_path / "esc.sh").write_text("\\. ./lib.sh\ncurl\n", encoding="utf-8")

    lines: dict[str, tuple[int, ...]] = {}
    for group in scan(tmp_path, cache=None).symbols:
        if group.symbol != "curl":
            continue
        for location in group.locations:
            lines[location.path] = location.lines

    assert lines["app.sh"] == (1,)
    assert lines["dyn.sh"] == (2,)
    assert "lib.sh" not in lines
    assert "nested/top.sh" not in lines
    assert "esc.sh" not in lines


def test_shell_source_with_an_assignment_or_a_previous_command(tmp_path: Path) -> None:
    (tmp_path / "lib.sh").write_text("curl() { :; }\n", encoding="utf-8")
    (tmp_path / "assigned.sh").write_text("NVM_ENV=testing \\. ./lib.sh\ncurl\n", encoding="utf-8")
    (tmp_path / "colon.sh").write_text(": nvm.sh\n\\. ./lib.sh\ncurl\n", encoding="utf-8")
    (tmp_path / "var.sh").write_text('\\. "$DIR/lib.sh"\ncurl\n', encoding="utf-8")

    lines: dict[str, tuple[int, ...]] = {}
    for group in scan(tmp_path, cache=None).symbols:
        if group.symbol != "curl":
            continue
        for location in group.locations:
            lines[location.path] = location.lines

    assert lines["var.sh"] == (2,)
    assert "assigned.sh" not in lines
    assert "colon.sh" not in lines


def test_dollar_variable_keeps_a_command_prefix(tmp_path: Path) -> None:
    (tmp_path / "build.sh").write_text(
        "build() {\n"
        '  make="curl${EXTRA-}"\n'
        '  make="wget${EXTRA-}"\n'
        "  $make -j 1\n"
        "}\n"
        "other() {\n"
        '  tool="curl${REQUIRED}"\n'
        "  $tool\n"
        "}\n",
        encoding="utf-8",
    )

    lines: dict[str, tuple[int, ...]] = {}
    for group in scan(tmp_path, cache=None).symbols:
        for location in group.locations:
            if location.path == "build.sh":
                lines[group.symbol] = location.lines

    assert lines["curl"] == (4,)
    assert lines["wget"] == (4,)


def test_functions_defined_in_the_repo_are_not_classified(tmp_path: Path) -> None:
    (tmp_path / "api.h").write_text("void curlx_fopen(const char *path);\n", encoding="utf-8")
    (tmp_path / "local.c").write_text("static void fopen(void) {}\n", encoding="utf-8")
    (tmp_path / "main.c").write_text(
        '#include <stdio.h>\n#include "api.h"\n'
        'int main(void) { fopen("a", "r"); curlx_fopen("a"); }\n',
        encoding="utf-8",
    )
    (tmp_path / "other.cpp").write_text("void from_cpp() {}\n", encoding="utf-8")
    (tmp_path / "use.c").write_text(
        'void from_cpp(void);\nint run(void) { from_cpp(); fopen("a", "r"); }\n',
        encoding="utf-8",
    )
    (tmp_path / "a.php").write_text("<?php\nfunction helper() {}\n", encoding="utf-8")
    (tmp_path / "b.php").write_text("<?php\nhelper();\nfile_get_contents('a');\n", encoding="utf-8")
    (tmp_path / "a.sh").write_text("helper() { :; }\n", encoding="utf-8")
    (tmp_path / "b.sh").write_text(". ./a.sh\nhelper\nmktemp\n", encoding="utf-8")
    (tmp_path / "a.ps1").write_text("function Helper {}\n", encoding="utf-8")
    (tmp_path / "b.ps1").write_text("helper\nGet-Content a\n", encoding="utf-8")

    found = {
        (group.language, group.module, group.symbol)
        for group in scan(tmp_path, cache=None, jobs=2).symbols
    }

    assert ("c", "", "fopen") in found
    assert ("c", "", "curlx_fopen") not in found
    assert ("c", "", "from_cpp") not in found
    assert ("php", "", "file_get_contents") in found
    assert ("php", "", "helper") not in found
    assert ("shell", "", "mktemp") in found
    assert ("shell", "", "helper") not in found
    assert ("powershell", "", "Get-Content") in found
    assert ("powershell", "", "helper") not in found


def test_c_macros_defined_in_the_repo_are_not_classified(tmp_path: Path) -> None:
    class Record:
        def __init__(self) -> None:
            self.seen: list[Symbol] = []

        def classify(self, symbols: list[Symbol]) -> list[Label]:
            self.seen.extend(symbols)
            return [Label(io=False, kinds=()) for _ in symbols]

    (tmp_path / "curl_setup.h").write_text(
        "#define CURLMIN(x, y) ((x) < (y) ? (x) : (y))\n"
        "#define CURL_SCLOSE(x) close(x)\n"
        "#define BIO_new BIO_NEW\n"
        "#define RECV_TYPE_ARG1 int\n"
        "#define sread(x, y, z) (ssize_t)recv((RECV_TYPE_ARG1)(x), (y), (z))\n"
        "#define call(fn, x) fn(x)\n"
        "#ifdef MINIX\n"
        "#define sread2(x) read(x)\n"
        "#else\n"
        "#define sread2(x) recv(x)\n"
        "#endif\n",
        encoding="utf-8",
    )
    (tmp_path / "main.c").write_text(
        "int main(void) { CURLMIN(1, 2); CURL_SCLOSE(1); BIO_new(0); fopen(\"a\", \"r\"); sread(1, 0, 0); }\n",
        encoding="utf-8",
    )
    client = Record()
    scan(tmp_path, client=client)
    seen = {(item.language, item.module, item.symbol) for item in client.seen}

    assert ("c", "", "CURLMIN") not in seen
    assert ("c", "", "CURL_SCLOSE") not in seen
    assert ("c", "", "sread") not in seen
    assert ("c", "", "call") not in seen
    assert ("c", "", "fn") not in seen
    assert ("c", "", "RECV_TYPE_ARG1") not in seen
    assert ("c", "", "close") in seen
    assert ("c", "", "recv") in seen
    assert ("c", "", "read") in seen
    assert ("c", "", "BIO_new") in seen
    assert ("c", "", "fopen") in seen


def test_headers_elsewhere_in_the_repo_are_not_classified(tmp_path: Path) -> None:
    class Record:
        def __init__(self) -> None:
            self.seen: list[Symbol] = []

        def classify(self, symbols: list[Symbol]) -> list[Label]:
            self.seen.extend(symbols)
            return [Label(io=False, kinds=()) for _ in symbols]

    lib = tmp_path / "lib"
    (lib / "vquic").mkdir(parents=True)
    (lib / "vtls").mkdir()
    (tmp_path / "include" / "curl").mkdir(parents=True)
    (lib / "http.h").write_text("#endif\n", encoding="utf-8")
    (lib / "vtls" / "openssl.h").write_text("#endif\n", encoding="utf-8")
    (tmp_path / "include" / "curl" / "curl.h").write_text("#endif\n", encoding="utf-8")
    (lib / "vquic" / "use.c").write_text(
        '#include "http.h"\n#include "vtls/openssl.h"\n#include <curl/curl.h>\n#include <stdio.h>\n'
        'int main(void) { fopen("a", "r"); }\n',
        encoding="utf-8",
    )
    client = Record()
    scan(tmp_path, client=client)
    seen = {(item.language, item.module, item.symbol) for item in client.seen}

    assert ("c", "http.h", "") not in seen
    assert ("c", "vtls/openssl.h", "") not in seen
    assert ("c", "curl/curl.h", "") not in seen
    assert ("c", "stdio.h", "") in seen
    assert ("c", "", "fopen") in seen


def test_packages_declared_in_the_repo_are_not_classified(tmp_path: Path) -> None:
    class Record:
        def __init__(self) -> None:
            self.seen: list[Symbol] = []

        def classify(self, symbols: list[Symbol]) -> list[Label]:
            self.seen.extend(symbols)
            return [Label(io=False, kinds=()) for _ in symbols]

    (tmp_path / "Foo.java").write_text(
        "package com.example;\npublic class Foo { void read() {} }\n",
        encoding="utf-8",
    )
    (tmp_path / "Main.java").write_text(
        "package com.other;\nimport com.example.Foo;\nimport java.nio.file.Files;\n"
        "public class Main { void m() throws Exception { Foo.read(); Foo.missing(); Files.readAllBytes(null); } }\n",
        encoding="utf-8",
    )
    (tmp_path / "App.kt").write_text(
        "package app\nimport com.example.Foo\nfun main() { Foo.read(); Foo.missing() }\n",
        encoding="utf-8",
    )
    internal = tmp_path / "internal"
    internal.mkdir()
    (internal / "io.go").write_text("package internal\nfunc Write() {}\n", encoding="utf-8")
    (tmp_path / "go.mod").write_text("module example.com/tool\n\ngo 1.22\n", encoding="utf-8")
    (tmp_path / "main.go").write_text(
        'package main\nimport (\n\t"example.com/tool/internal"\n\t"os"\n)\n'
        'func main() { os.Remove("a"); internal.Write(); internal.Read() }\n',
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text('{"name": "app"}\n', encoding="utf-8")
    (tmp_path / "lib.js").write_text("export function read() {}\n", encoding="utf-8")
    (tmp_path / "web.js").write_text(
        "import fs from 'fs'\nimport local from 'app/lib'\n"
        "fs.readFileSync('a')\nlocal.read()\nlocal.write()\n",
        encoding="utf-8",
    )
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")
    source = tmp_path / "src"
    source.mkdir()
    (source / "fs.rs").write_text("pub fn read() {}\n", encoding="utf-8")
    (source / "lib.rs").write_text(
        "use demo::fs::read;\nuse demo::fs::missing;\nuse std::fs::read;\n",
        encoding="utf-8",
    )
    record = Record()

    scan(tmp_path, client=record, cache=None, jobs=2)
    seen = {(symbol.language, symbol.module, symbol.symbol) for symbol in record.seen}

    assert ("java", "java.nio.file.Files", "readAllBytes") in seen
    assert ("java", "com.example.Foo", "read") not in seen
    assert ("java", "com.example.Foo", "missing") in seen
    assert ("kotlin", "com.example.Foo", "read") not in seen
    assert ("kotlin", "com.example.Foo", "missing") in seen
    assert ("go", "os", "Remove") in seen
    assert ("go", "example.com/tool/internal", "Write") not in seen
    assert ("go", "example.com/tool/internal", "Read") in seen
    assert ("javascript", "fs", "readFileSync") in seen
    assert ("javascript", "app/lib", "read") not in seen
    assert ("javascript", "app/lib", "write") in seen
    assert ("rust", "std::fs", "read") in seen
    assert ("rust", "demo::fs", "read") not in seen
    assert ("rust", "demo::fs", "missing") in seen


def test_go_package_values_are_internal(tmp_path: Path) -> None:
    class Record:
        def __init__(self) -> None:
            self.seen: list[Symbol] = []

        def classify(self, symbols: list[Symbol]) -> list[Label]:
            self.seen.extend(symbols)
            return [Label(io=False, kinds=()) for _ in symbols]

    (tmp_path / "go.mod").write_text("module example.com/tool\n\ngo 1.22\n", encoding="utf-8")
    internal = tmp_path / "internal"
    internal.mkdir()
    (internal / "messages.go").write_text(
        "package internal\n"
        "func Write() {}\n"
        "var Hello = 1\n"
        "var (\n\tOne = 1\n\tTwo, Three int = 2, 3\n)\n"
        "const Answer = 1\n"
        "func local() { var Hidden = 1 }\n",
        encoding="utf-8",
    )
    (tmp_path / "main.go").write_text(
        "package main\n"
        "import (\n\t\"example.com/tool/internal\"\n\t\"os\"\n)\n"
        "func main() {\n"
        "\tos.Remove(\"a\")\n"
        "\tinternal.Write()\n"
        "\tinternal.Read()\n"
        "\tinternal.Hello.Code()\n"
        "\tinternal.One.Localize()\n"
        "\tinternal.Three.Code()\n"
        "\tinternal.Answer.Localize()\n"
        "\tinternal.Missing.Code()\n"
        "\tinternal.Hidden.Code()\n"
        "}\n",
        encoding="utf-8",
    )
    record = Record()

    scan(tmp_path, client=record, cache=None)
    seen = {(symbol.language, symbol.module, symbol.symbol) for symbol in record.seen}

    assert ("go", "os", "Remove") in seen
    assert ("go", "example.com/tool/internal", "Read") in seen
    assert ("go", "example.com/tool/internal", "Missing.Code") in seen
    assert ("go", "example.com/tool/internal", "Hidden.Code") in seen
    assert ("go", "example.com/tool/internal", "Write") not in seen
    assert ("go", "example.com/tool/internal", "Hello.Code") not in seen
    assert ("go", "example.com/tool/internal", "One.Localize") not in seen
    assert ("go", "example.com/tool/internal", "Three.Code") not in seen
    assert ("go", "example.com/tool/internal", "Answer.Localize") not in seen


def test_csharp_methods_declared_in_the_repo_are_not_classified(tmp_path: Path) -> None:
    class Record:
        def __init__(self) -> None:
            self.seen: list[Symbol] = []

        def classify(self, symbols: list[Symbol]) -> list[Label]:
            self.seen.extend(symbols)
            return [Label(io=False, kinds=()) for _ in symbols]

    (tmp_path / "Lib.cs").write_text(
        "namespace App.Tools;\n"
        "class File { public static void ReadAllText(string path) {} }\n",
        encoding="utf-8",
    )
    (tmp_path / "Program.cs").write_text(
        "using App.Tools;\n"
        "using System.Net;\n"
        "namespace App.Tools;\n"
        "class Program {\n"
        "  static void Main() {\n"
        "    File.ReadAllText(\"a\");\n"
        "    Directory.Delete(\"b\");\n"
        "    global::System.IO.Directory.CreateDirectory(\"c\");\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    record = Record()

    scan(tmp_path, client=record, cache=None)
    seen = {(symbol.language, symbol.module, symbol.symbol) for symbol in record.seen}

    assert ("csharp", "File", "ReadAllText") not in seen
    assert ("csharp", "App.Tools.File", "ReadAllText") not in seen
    assert ("csharp", "Directory", "Delete") in seen
    assert ("csharp", "System.IO.Directory", "CreateDirectory") in seen
    assert ("csharp", "App.Tools", "") not in seen
    assert ("csharp", "System.Net", "") in seen


def test_a_symbol_declared_in_another_root_is_internal(tmp_path: Path) -> None:
    class Record:
        def __init__(self) -> None:
            self.seen: list[Symbol] = []

        def classify(self, symbols: list[Symbol]) -> list[Label]:
            self.seen.extend(symbols)
            return [Label(io=True, kinds=("filesystem",)) for _ in symbols]

    left = tmp_path / "left"
    right = tmp_path / "right"
    (left / "pkg").mkdir(parents=True)
    (left / "go.mod").write_text("module example.com/left\n\ngo 1.22\n", encoding="utf-8")
    (left / "pkg" / "lib.go").write_text("package pkg\nfunc Read() {}\n", encoding="utf-8")
    (right).mkdir()
    (right / "go.mod").write_text("module example.com/right\n\ngo 1.22\n", encoding="utf-8")
    (right / "main.go").write_text(
        "package main\n"
        "import (\n"
        "\t\"example.com/left/pkg\"\n"
        "\t\"os\"\n"
        ")\n"
        "func main() {\n"
        "\tpkg.Read()\n"
        "\tos.Remove(\"a\")\n"
        "}\n",
        encoding="utf-8",
    )
    record = Record()

    report = scan(left, right, client=record, cache=None)
    seen = {(symbol.language, symbol.module, symbol.symbol) for symbol in record.seen}

    assert ("go", "example.com/left/pkg", "Read") not in seen
    assert ("go", "os", "Remove") in seen
    assert report.root == f"{left.resolve()}\n{right.resolve()}"
    assert {location.path for group in report.symbols for location in group.locations} == {"right/main.go"}


def test_go_types_build_package_and_shadowing(tmp_path: Path) -> None:
    class Record:
        def __init__(self) -> None:
            self.seen: list[Symbol] = []

        def classify(self, symbols: list[Symbol]) -> list[Label]:
            self.seen.extend(symbols)
            return [Label(io=False, kinds=()) for _ in symbols]

    (tmp_path / "go.mod").write_text("module example.com/tool\n\ngo 1.22\n", encoding="utf-8")
    tspath = tmp_path / "tspath"
    tspath.mkdir()
    padding = "\n".join(f"func pad{index}() {{}}" for index in range(80))
    (tspath / "path.go").write_text(
        "package tspath\n"
        "type Path string\n"
        f"{padding}\n"
        "func (p Path) ForEachAncestorDirectory[T any](callback func(directory Path) (result T, stop bool)) (result T, ok bool) {\n"
        "\treturn\n"
        "}\n"
        "func HasExtension(fileName string) bool { return false }\n",
        encoding="utf-8",
    )
    build = tmp_path / "build"
    build.mkdir()
    (build / "orch.go").write_text(
        "package build\nfunc NewOrchestrator() {}\n",
        encoding="utf-8",
    )
    parser = tmp_path / "parser"
    parser.mkdir()
    (parser / "parser.go").write_text("package parser\nfunc Real() {}\n", encoding="utf-8")
    (tmp_path / "main.go").write_text(
        "package main\n"
        "import (\n"
        "\t\"example.com/tool/build\"\n"
        "\t\"example.com/tool/parser\"\n"
        "\t\"example.com/tool/project\"\n"
        "\t\"example.com/tool/tspath\"\n"
        "\t\"example.com/tool/vfs\"\n"
        "\t\"os\"\n"
        ")\n"
        "func main() {\n"
        "\ttspath.HasExtension(\"a\")\n"
        "\ttspath.Path(\"a\")\n"
        "\tbuild.NewOrchestrator()\n"
        "\tparser.Real()\n"
        "\tparser.Missing()\n"
        "\tos.Remove(\"a\")\n"
        "}\n"
        "func local() {\n"
        "\tparser := 1\n"
        "\tparser.parse()\n"
        "\tgo func() {\n"
        "\t\tproject := 1\n"
        "\t\tproject.File()\n"
        "\t}()\n"
        "}\n"
        "func (vfs *box) Read() {\n"
        "\tvfs.fs.GetAccessibleEntries()\n"
        "}\n",
        encoding="utf-8",
    )
    record = Record()

    scan(tmp_path, client=record, cache=None)
    seen = {(symbol.language, symbol.module, symbol.symbol) for symbol in record.seen}

    assert ("go", "os", "Remove") in seen
    assert ("go", "example.com/tool/parser", "Missing") in seen
    assert ("go", "example.com/tool/tspath", "HasExtension") not in seen
    assert ("go", "example.com/tool/tspath", "Path") not in seen
    assert ("go", "example.com/tool/build", "NewOrchestrator") not in seen
    assert ("go", "example.com/tool/parser", "Real") not in seen
    assert ("go", "example.com/tool/parser", "parse") not in seen
    assert ("go", "example.com/tool/project", "File") not in seen
    assert ("go", "example.com/tool/vfs", "fs.GetAccessibleEntries") not in seen


def test_js_workspace_specifiers_are_internal(tmp_path: Path) -> None:
    class Record:
        def __init__(self) -> None:
            self.seen: list[Symbol] = []

        def classify(self, symbols: list[Symbol]) -> list[Label]:
            self.seen.extend(symbols)
            return [Label(io=False, kinds=()) for _ in symbols]

    package = tmp_path / "packages" / "vite"
    package.mkdir(parents=True)
    (package / "package.json").write_text(
        '{"name": "vite", "imports": {"#dep-types/*": "./src/types/*.d.ts"}, '
        '"exports": {".": "./dist/node/index.js", "./module-runner": "./dist/node/module-runner.js"}}\n',
        encoding="utf-8",
    )
    legacy = tmp_path / "packages" / "plugin-legacy"
    legacy.mkdir()
    (legacy / "package.json").write_text('{"name": "@vitejs/plugin-legacy"}\n', encoding="utf-8")
    playground = tmp_path / "playground"
    playground.mkdir()
    (playground / "package.json").write_text('{"name": "@vitejs/vite-playground"}\n', encoding="utf-8")
    (playground / "tsconfig.json").write_text(
        '{"compilerOptions": {"paths": {"~utils": ["./test-utils.ts"]}}}\n',
        encoding="utf-8",
    )
    (playground / "use.ts").write_text(
        "import { editFile, page } from '~utils'\n"
        "import { createServer } from 'vite'\n"
        "import { ModuleRunner } from 'vite/module-runner'\n"
        "import type { Connect } from '#dep-types/connect'\n"
        "import { test } from 'vitest'\n"
        "import fs from 'node:fs'\n"
        "import { read } from 'vite/src/secret'\n"
        "editFile('a')\n"
        "page.goto('http://example')\n"
        "createServer()\n"
        "fs.readFileSync('a')\n"
        "test('name', () => {})\n",
        encoding="utf-8",
    )
    (playground / "legacy.js").write_text(
        "const fs = require('node:fs')\n"
        "const legacy = require('@vitejs/plugin-legacy')\n"
        "fs.readFileSync('a')\n"
        "legacy()\n",
        encoding="utf-8",
    )
    record = Record()

    scan(tmp_path, client=record, cache=None)
    seen = {(symbol.language, symbol.module, symbol.symbol) for symbol in record.seen}

    assert ("typescript", "node:fs", "readFileSync") in seen
    assert ("typescript", "vitest", "test") in seen
    assert ("typescript", "vite/src/secret", "read") in seen
    assert ("typescript", "~utils", "editFile") not in seen
    assert ("typescript", "~utils", "page.goto") not in seen
    assert ("typescript", "vite", "createServer") not in seen
    assert ("typescript", "vite/module-runner", "ModuleRunner") not in seen
    assert ("typescript", "#dep-types/connect", "Connect") not in seen
    assert ("javascript", "node:fs", "readFileSync") in seen
    assert ("javascript", "@vitejs/plugin-legacy", "") not in seen


def test_jvm_fields_and_properties_are_internal(tmp_path: Path) -> None:
    class Record:
        def __init__(self) -> None:
            self.seen: list[Symbol] = []

        def classify(self, symbols: list[Symbol]) -> list[Label]:
            self.seen.extend(symbols)
            return [Label(io=False, kinds=()) for _ in symbols]

    (tmp_path / "Foo.java").write_text(
        "package com.example;\n"
        "public class Foo {\n"
        "  public static final int TYPE_DATA = 0;\n"
        "  void read() {}\n"
        "}\n"
        "enum Kind { H2 }\n",
        encoding="utf-8",
    )
    (tmp_path / "Main.java").write_text(
        "package com.other;\n"
        "import com.example.Foo;\n"
        "import static com.example.Foo.TYPE_DATA;\n"
        "import com.example.Kind.H2;\n"
        "import java.nio.file.Files;\n"
        "public class Main {\n"
        "  void m() throws Exception { Foo.read(); Foo.missing(); Files.readAllBytes(null); }\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "Http2.kt").write_text(
        "package okhttp3.internal.http2\n"
        "object Http2 {\n"
        "  const val TYPE_DATA = 0\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "Authenticator.kt").write_text(
        "package okhttp3\n"
        "class Authenticator {\n"
        "  companion object {\n"
        "    val JAVA_NET_AUTHENTICATOR = Authenticator()\n"
        "  }\n"
        "  fun authenticate() {}\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "Use.kt").write_text(
        "package app\n"
        "import okhttp3.internal.http2.Http2.TYPE_DATA\n"
        "import okhttp3.Authenticator.Companion.JAVA_NET_AUTHENTICATOR\n"
        "import java.nio.file.Files\n"
        "fun main() {\n"
        "  JAVA_NET_AUTHENTICATOR.authenticate()\n"
        "  Files.readAllBytes(null)\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "Accessors.kt").write_text(
        "package okhttp3.internal\n"
        "class Exchange\n"
        "internal val Exchange.connectionAccessor\n"
        "  get() = 1\n",
        encoding="utf-8",
    )
    (tmp_path / "Open.kt").write_text(
        "package okhttp3\n"
        "import okhttp3.internal.connectionAccessor\n"
        "fun main() {}\n",
        encoding="utf-8",
    )
    (tmp_path / "EventSources.kt").write_text(
        "package okhttp3.sse\n"
        "object EventSources {\n"
        "  fun createFactory() = 1\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "EventTest.kt").write_text(
        "package t\n"
        "import okhttp3.sse.EventSources.createFactory\n"
        "fun main() { createFactory() }\n",
        encoding="utf-8",
    )
    (tmp_path / "CancelTest.kt").write_text(
        "package okhttp3.internal.http\n"
        "class CancelTest {\n"
        "  enum class ConnectionType { H2, HTTP }\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "CancelUse.kt").write_text(
        "package t\n"
        "import okhttp3.internal.http.CancelTest.ConnectionType.H2\n"
        "fun main() {}\n",
        encoding="utf-8",
    )
    record = Record()

    scan(tmp_path, client=record, cache=None)
    seen = {(symbol.language, symbol.module, symbol.symbol) for symbol in record.seen}

    assert ("java", "java.nio.file.Files", "readAllBytes") in seen
    assert ("java", "com.example.Foo", "read") not in seen
    assert ("java", "com.example.Foo", "missing") in seen
    assert ("java", "com.example.Foo.TYPE_DATA", "") not in seen
    assert ("java", "com.example.Kind.H2", "") not in seen
    assert ("kotlin", "java.nio.file.Files", "readAllBytes") in seen
    assert ("kotlin", "okhttp3.internal.http2.Http2.TYPE_DATA", "") not in seen
    assert ("kotlin", "okhttp3.Authenticator.Companion.JAVA_NET_AUTHENTICATOR", "authenticate") not in seen
    assert ("kotlin", "okhttp3.internal.connectionAccessor", "") not in seen
    assert ("kotlin", "okhttp3.sse.EventSources.createFactory", "") not in seen
    assert ("kotlin", "okhttp3.internal.http.CancelTest.ConnectionType.H2", "") not in seen


def test_kotlin_nested_type_and_companion_are_internal(tmp_path: Path) -> None:
    class Record:
        def __init__(self) -> None:
            self.seen: list[Symbol] = []

        def classify(self, symbols: list[Symbol]) -> list[Label]:
            self.seen.extend(symbols)
            return [Label(io=False, kinds=()) for _ in symbols]

    (tmp_path / "Http2Connection.kt").write_text(
        "package okhttp3.internal.http2\n"
        "class Http2Connection {\n"
        "  class Builder\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "CertificatePinner.kt").write_text(
        "package okhttp3\n"
        "class CertificatePinner {\n"
        "  companion object {\n"
        "    fun pin() = 1\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "Internal.kt").write_text(
        "package okhttp3.internal\n"
        "class Response\n"
        "internal val Response.connection get() = 1\n",
        encoding="utf-8",
    )
    (tmp_path / "Foo.kt").write_text(
        "package com.example\n"
        "class Foo\n",
        encoding="utf-8",
    )
    (tmp_path / "Use.kt").write_text(
        "package app\n"
        "import okhttp3.internal.http2.Http2Connection\n"
        "import okhttp3.CertificatePinner\n"
        "import okhttp3.internal.connection\n"
        "import com.example.Foo\n"
        "import java.nio.file.Files\n"
        "fun main() {\n"
        "  Http2Connection.Builder()\n"
        "  CertificatePinner.pin()\n"
        "  connection.socket()\n"
        "  Foo.hashCode()\n"
        "  Files.readAllBytes(null)\n"
        "}\n",
        encoding="utf-8",
    )
    record = Record()

    scan(tmp_path, client=record, cache=None)
    seen = {(symbol.language, symbol.module, symbol.symbol) for symbol in record.seen}

    assert ("kotlin", "okhttp3.internal.http2.Http2Connection", "Builder") not in seen
    assert ("kotlin", "okhttp3.CertificatePinner", "pin") not in seen
    assert ("kotlin", "okhttp3.internal.connection", "socket") not in seen
    assert ("kotlin", "com.example.Foo", "hashCode") in seen
    assert ("kotlin", "java.nio.file.Files", "readAllBytes") in seen


def test_cpp_methods_stay_with_their_class(tmp_path: Path) -> None:
    class Record:
        def __init__(self) -> None:
            self.seen: list[Symbol] = []

        def classify(self, symbols: list[Symbol]) -> list[Label]:
            self.seen.extend(symbols)
            return [Label(io=False, kinds=()) for _ in symbols]

    (tmp_path / "copier.h").write_text(
        "class StreamCopier {\n"
        "public:\n"
        "    static int copyStream();\n"
        "    std::string label;\n"
        "};\n",
        encoding="utf-8",
    )
    (tmp_path / "use.cpp").write_text(
        '#include "copier.h"\n'
        '#include <stdio.h>\n'
        "int StreamCopier::copyStream() { return 0; }\n"
        "void other() { StreamCopier::copyStream(); Poco::StreamCopier::copyStream(); read(0, 0, 0); }\n"
        "void win() { ::CreateFileW(0); ::read(0, 0, 0); ::Poco::StreamCopier::copyStream(); }\n",
        encoding="utf-8",
    )
    (tmp_path / "handler.h").write_text(
        "class ClientServiceHandler {\n"
        "public:\n"
        "    void doSomething();\n"
        "    std::string label;\n"
        "};\n"
        "class HeaderGenerator {\n"
        "public:\n"
        "    void writeBeginNameSpace();\n"
        "    void generate();\n"
        "    std::string label;\n"
        "};\n",
        encoding="utf-8",
    )
    (tmp_path / "handler.cpp").write_text(
        '#include "handler.h"\n'
        "void ClientServiceHandler::doSomething() { doSomething(); read(0, 0, 0); }\n"
        "void HeaderGenerator::generate() { writeBeginNameSpace(); fopen(\"a\", \"r\"); }\n"
        "void free_fn() { doSomething(); read(0, 0, 0); }\n"
        "class TextEncoding {\n"
        "public:\n"
        "    void manager();\n"
        "};\n"
        "TextEncoding& TextEncoding::byName() { manager(); read(0, 0, 0); }\n"
        "TextEncoding* TextEncoding::find() { manager(); }\n",
        encoding="utf-8",
    )
    fields = "\n".join(f"int field_{index};" for index in range(40))
    (tmp_path / "remote.h").write_text(
        "struct Box {\n" + fields + "\n};\n"
        'static const std::string REMOTING__NAMESPACE("http://example.com/"s);\n',
        encoding="utf-8",
    )
    record = Record()

    scan(tmp_path, client=record, cache=None)
    seen = {(symbol.language, symbol.module, symbol.symbol) for symbol in record.seen}

    assert ("cpp", "StreamCopier", "copyStream") not in seen
    assert ("cpp", "Poco", "copyStream") not in seen
    assert ("cpp", "", "copyStream") not in seen
    assert ("cpp", "", "CreateFileW") in seen
    assert ("cpp", "", "writeBeginNameSpace") not in seen
    assert ("cpp", "", "doSomething") in seen
    assert ("cpp", "", "manager") not in seen
    assert ("cpp", "", "read") in seen
    assert ("cpp", "", "fopen") in seen
    assert ("cpp", "stdio.h", "") in seen
    assert all(symbol.symbol != "REMOTING__NAMESPACE" for symbol in record.seen)


def test_php_leading_backslash_is_the_global_function(tmp_path: Path) -> None:
    (tmp_path / "client.php").write_text(
        "<?php\n"
        "namespace App {\n"
        "    function run($handle) {\n"
        "        \\file_get_contents('a');\n"
        "        \\curl_exec($handle);\n"
        "        Foo\\file_get_contents('b');\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    report = scan(tmp_path, cache=None)
    found = {(group.language, group.module, group.symbol) for group in report.symbols}

    assert ("php", "", "file_get_contents") in found
    assert ("php", "", "curl_exec") in found
    assert all("Foo" not in group.symbol for group in report.symbols)


def test_aliases_are_reported_as_the_original_symbol(tmp_path: Path) -> None:
    (tmp_path / "alias.js").write_text(
        'import * as filesystem from "fs"\n'
        "const { readFileSync: rf } = require('fs')\n"
        "filesystem.readFileSync('a')\n"
        "rf('a')\n",
        encoding="utf-8",
    )
    (tmp_path / "alias.kt").write_text(
        "import java.nio.file.Files as F\nfun main() { F.readAllBytes(null) }\n",
        encoding="utf-8",
    )
    (tmp_path / "alias.php").write_text(
        "<?php\nuse function file_get_contents as fgc;\nfgc('a');\n",
        encoding="utf-8",
    )
    (tmp_path / "alias.ps1").write_text("gc a\ncurl https://example.com\n", encoding="utf-8")

    report = scan(tmp_path, cache=None)
    found = {(group.language, group.module, group.symbol) for group in report.symbols}

    assert ("javascript", "fs", "readFileSync") in found
    assert ("javascript", "fs", "filesystem") not in found
    assert ("javascript", "fs", "rf") not in found
    assert ("kotlin", "java.nio.file.Files", "readAllBytes") in found
    assert ("php", "", "file_get_contents") in found
    assert ("php", "", "fgc") not in found
    assert ("powershell", "", "Get-Content") in found
    assert ("powershell", "", "Invoke-WebRequest") in found


def test_rust_reexports_follow_the_path_written_in_source(tmp_path: Path) -> None:
    class Record:
        def __init__(self) -> None:
            self.seen: list[Symbol] = []

        def classify(self, symbols: list[Symbol]) -> list[Label]:
            self.seen.extend(symbols)
            return [Label(io=False, kinds=()) for _ in symbols]

    def package(directory: Path, name: str) -> None:
        directory.mkdir(parents=True)
        (directory / "Cargo.toml").write_text(
            f'[package]\nname = "{name}"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
        (directory / "src").mkdir()

    cli = tmp_path / "crates" / "grep-cli"
    package(cli, "grep-cli")
    (cli / "src" / "decompress.rs").write_text(
        "pub fn resolve_binary() {}\n",
        encoding="utf-8",
    )
    (cli / "src" / "lib.rs").write_text(
        "mod decompress;\npub use decompress::resolve_binary;\n",
        encoding="utf-8",
    )
    facade = tmp_path / "crates" / "grep"
    package(facade, "grep")
    (facade / "src" / "lib.rs").write_text(
        "pub extern crate grep_cli as cli;\n",
        encoding="utf-8",
    )
    searcher = tmp_path / "crates" / "grep-searcher"
    package(searcher, "grep-searcher")
    (searcher / "src" / "lib.rs").write_text(
        "pub struct Searcher;\n",
        encoding="utf-8",
    )
    app = tmp_path / "crates" / "app"
    package(app, "app")
    (app / "src" / "main.rs").write_text(
        "use grep_searcher::Searcher;\n"
        "struct WalkBuilder;\n"
        "impl WalkBuilder { fn new() {} }\n"
        "enum Error { Io(u8) }\n"
        "fn main() {\n"
        "    grep::cli::resolve_binary();\n"
        "    grep::cli::missing();\n"
        "    WalkBuilder::new();\n"
        "    Error::Io(1);\n"
        "    let _searcher = Searcher;\n"
        '    std::fs::read("a");\n'
        "}\n",
        encoding="utf-8",
    )
    record = Record()

    scan(tmp_path, client=record, cache=None)
    seen = {(symbol.language, symbol.module, symbol.symbol) for symbol in record.seen}

    assert ("rust", "grep::cli", "resolve_binary") not in seen
    assert ("rust", "grep_searcher", "Searcher") not in seen
    assert ("rust", "WalkBuilder", "new") not in seen
    assert ("rust", "Error", "Io") not in seen
    assert ("rust", "grep::cli", "missing") in seen
    assert ("rust", "std::fs", "read") in seen


def test_rust_associated_call_uses_the_imported_type(tmp_path: Path) -> None:
    (tmp_path / "read.rs").write_text(
        "use std::{fs::File};\n"
        "fn main() {\n"
        '    let _ = File::open("a");\n'
        '    let _ = std::fs::File::open("b");\n'
        "}\n",
        encoding="utf-8",
    )

    report = scan(tmp_path, cache=None)
    groups = {(group.module, group.symbol): group for group in report.symbols}

    opened = groups[("std::fs::File", "open")]
    assert opened.kinds == ("filesystem",)
    assert opened.locations[0].lines == (3, 4)


def test_scan_rejects_nonpositive_jobs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="jobs"):
        scan(tmp_path, cache=None, jobs=0)


def test_report_text_and_json_group_symbols_by_file() -> None:
    report = Report(
        version=3,
        root="/repo",
        symbols=(
            SymbolGroup("go", "os", "ReadFile", ("filesystem",), (Location("a.go", (4, 9)),)),
            SymbolGroup("python", "os", "", ("filesystem",), (Location("b.py", (2,)), Location("c.py", (8, 11)))),
            SymbolGroup("python", "requests", "get", ("network",), (Location("b.py", (3,)),)),
        ),
    )

    assert report.to_text() == (
        "a.go\n"
        "  filesystem  go  os.ReadFile  4,9\n"
        "\n"
        "b.py\n"
        "  filesystem  python  os  2\n"
        "  network  python  requests.get  3\n"
        "\n"
        "c.py\n"
        "  filesystem  python  os  8,11\n"
    )
    dumped = report.to_dict()
    assert dumped["version"] == 3
    assert dumped["files"][1]["path"] == "b.py"
    assert dumped["files"][1]["symbols"][0] == {
        "language": "python",
        "module": "os",
        "symbol": "",
        "kinds": ["filesystem"],
        "lines": [2],
    }
    assert dumped["files"][2]["symbols"][0]["lines"] == [8, 11]


def test_cli_defaults_jobs_to_available_cores() -> None:
    assert available_cores() >= 1


def test_cli_rejects_nonpositive_jobs(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main([str(tmp_path), "--jobs", "0"])


def test_cli_selects_text_or_json(tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FINDIO_MODEL", raising=False)
    (tmp_path / "main.go").write_text(
        'package main\nimport "os"\nfunc main() { os.Remove("a") }\n',
        encoding="utf-8",
    )
    written = tmp_path / "report.json"

    assert main([str(tmp_path), "--jobs", "1", "--format", "json", "--output", str(written)]) == 0
    assert '"files"' in written.read_text(encoding="utf-8")

    assert main([str(tmp_path), "--jobs", "2", "--format", "text"]) == 0
    assert capsys.readouterr().out == "main.go\n  filesystem  go  os.Remove  3\n"
