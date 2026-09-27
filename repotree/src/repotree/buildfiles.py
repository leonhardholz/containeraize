"""Recognize build and deployment files by name or by a short text probe."""

from __future__ import annotations

import re
from pathlib import Path

_PROBE_BYTES = 32 * 1024

_EXACT = frozenset(
    {
        "Makefile",
        "makefile",
        "GNUmakefile",
        "BSDmakefile",
        "Makefile.am",
        "makefile.am",
        "Makefile.in",
        "makefile.in",
        "Justfile",
        "justfile",
        "Taskfile.yml",
        "Taskfile.yaml",
        "Rakefile",
        "Dockerfile",
        "Containerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
        "Jenkinsfile",
        ".gitlab-ci.yml",
        "azure-pipelines.yml",
        ".travis.yml",
        "appveyor.yml",
        ".drone.yml",
        "cloudbuild.yaml",
        "cloudbuild.yml",
        "buildspec.yml",
        "appspec.yml",
        "Procfile",
        "fly.toml",
        "render.yaml",
        "serverless.yml",
        "serverless.yaml",
        "skaffold.yaml",
        "skaffold.yml",
        "Chart.yaml",
        "Chart.yml",
        "kustomization.yaml",
        "kustomization.yml",
        "Pulumi.yaml",
        "Pulumi.yml",
        "Vagrantfile",
        "ansible.cfg",
    }
)

_SUFFIXES = (
    ".mk",
    ".mak",
    ".make",
    ".dockerfile",
    ".tf",
    ".tfvars",
    ".tf.json",
    ".bicep",
    ".nomad",
    ".pkr.hcl",
    ".pkr.json",
    ".pp",
    ".sls",
)

_MARKDOWN = {".md", ".markdown", ".mdown", ".rst"}

_TOP_KEY = re.compile(r"""^(?:['"]?)([A-Za-z_][\w.-]*)(?:['"]?)\s*:""")
_FROM = re.compile(r"(?m)^FROM\s+\S")
_DOCKER_STEP = re.compile(r"(?m)^(RUN|COPY|ADD|CMD|ENTRYPOINT|WORKDIR)\s")
_MAKE_DIRECTIVE = re.compile(r"(?m)^\.(PHONY|SUFFIXES)\b")
_MAKE_DEFINE = re.compile(r"(?m)^define[ \t]+\S")
_MAKE_ENDEF = re.compile(r"(?m)^endef\s*$")
_MAKE_IF = re.compile(r"(?m)^if(eq|def|neq|ndef)[ \t]+\S")
_CF_TYPE = re.compile(r"""['"]?Type['"]?\s*:\s*['"]?AWS::""")
_CF_TRANSFORM = re.compile(r"""['"]?Transform['"]?\s*:\s*['"]?AWS::Serverless""")
_ARM_TYPE = re.compile(r"""['"]type['"]\s*:\s*['"]Microsoft\.""")
_ANSIBLE_HOSTS = re.compile(r"(?m)^[ \t-]*hosts\s*:")
_ANSIBLE_BODY = re.compile(r"(?m)^[ \t-]*(tasks|roles|pre_tasks|handlers)\s*:")
_K8S_API = re.compile(r"""['"]?apiVersion['"]?\s*:""")
_K8S_KIND = re.compile(r"""['"]?kind['"]?\s*:""")
_COMPOSE_NESTED = re.compile(r"""(?m)^[ \t-]*['"]?(image|build)['"]?\s*:""")


def classify_file(path: Path, relative: str) -> tuple[str, str]:
    """Return ``(kind, label)`` for one file.

    ``kind`` is ``marker``, ``build``, or ``file``. ``label`` is the language
    for a counted file and empty otherwise. Manifests win over build names.
    A named build file wins over the content probes.
    """
    from repotree.markers import is_marker, is_module_manifest, language_of

    name = path.name
    if is_marker(name):
        return "marker", ""
    if name.lower().endswith(".psd1"):
        text = _probe(path)
        if text is not None and is_module_manifest(text):
            return "marker", ""
        return "file", language_of(name)
    if is_named_build(name, relative):
        return "build", ""
    if path.suffix.lower() in _MARKDOWN:
        return "file", language_of(name)
    text = _probe(path)
    if text is not None and _content_build(name, text):
        return "build", ""
    return "file", language_of(name)


def is_named_build(name: str, relative: str) -> bool:
    """True when the filename or suffix is a known build or deploy file."""
    if name in _EXACT:
        return True
    if name.startswith(("Dockerfile.", "Containerfile.", "Jenkinsfile.")):
        return True
    lower = name.lower()
    if lower.endswith(_SUFFIXES):
        return True
    rel = relative.replace("\\", "/")
    return rel == ".circleci/config.yml" or rel.endswith("/.circleci/config.yml")


def _probe(path: Path) -> str | None:
    """First 32 KiB of text, or None when the prefix contains a NUL."""
    try:
        with path.open("rb") as handle:
            data = handle.read(_PROBE_BYTES)
    except OSError:
        return None
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")


def _content_build(name: str, text: str) -> bool:
    lower = name.lower()
    if lower.endswith((".yml", ".yaml")):
        return _yaml_build(text)
    if lower.endswith(".json"):
        return _json_build(text)
    return _is_docker(text) or _is_make(text)


def _yaml_build(text: str) -> bool:
    if _is_cloudformation(text):
        return True
    keys = _top_level_keys(text)
    if "on" in keys and "jobs" in keys:
        return True
    if _is_ansible(text):
        return True
    if "services" in keys and _COMPOSE_NESTED.search(text):
        return True
    return _is_kubernetes(text)


def _json_build(text: str) -> bool:
    if _is_cloudformation(text):
        return True
    if "deploymentTemplate.json" in text or _ARM_TYPE.search(text):
        return True
    return _is_kubernetes(text)


def _is_cloudformation(text: str) -> bool:
    if "AWSTemplateFormatVersion" in text:
        return True
    if _CF_TRANSFORM.search(text):
        return True
    return _CF_TYPE.search(text) is not None


def _is_ansible(text: str) -> bool:
    return _ANSIBLE_HOSTS.search(text) is not None and _ANSIBLE_BODY.search(text) is not None


def _is_kubernetes(text: str) -> bool:
    return _K8S_API.search(text) is not None and _K8S_KIND.search(text) is not None


def _is_docker(text: str) -> bool:
    return _FROM.search(text) is not None and _DOCKER_STEP.search(text) is not None


def _is_make(text: str) -> bool:
    if _MAKE_DIRECTIVE.search(text):
        return True
    if _MAKE_DEFINE.search(text) and _MAKE_ENDEF.search(text):
        return True
    return _MAKE_IF.search(text) is not None


def _top_level_keys(text: str) -> set[str]:
    keys: set[str] = set()
    for line in text.splitlines():
        if not line or line[0] in " \t#":
            continue
        if line.startswith(("---", "...")):
            continue
        match = _TOP_KEY.match(line)
        if match:
            keys.add(match.group(1))
    return keys
