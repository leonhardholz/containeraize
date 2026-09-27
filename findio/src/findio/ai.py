"""Ask LiteLLM to classify symbols the static table does not know."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
import time
from collections.abc import Callable
from typing import Any

from findio.report import Label, Symbol

PROMPT_VERSION = "3"
BATCH_SIZE = 30
# A stalled provider read would otherwise block until the process is killed.
COMPLETION_TIMEOUT = 60
_RETRY_ATTEMPTS = 3
_KINDS = ("filesystem", "network", "service", "process", "database")
_RETRY_IN = re.compile(r"retry in ([0-9]+(?:\.[0-9]+)?)s", re.IGNORECASE)
_RETRY_DELAY = re.compile(r'"retryDelay"\s*:\s*"([0-9]+(?:\.[0-9]+)?)s"')
# Transient provider failures are retried. A bad key or a bad request is not.
# Rate limits are retried here, so the provider's delay is the wait.
_RETRY_POLICY = {
    "RateLimitErrorRetries": 0,
    "InternalServerErrorRetries": _RETRY_ATTEMPTS,
    "ServiceUnavailableErrorRetries": _RETRY_ATTEMPTS,
    "TimeoutErrorRetries": _RETRY_ATTEMPTS,
    "DefaultRetries": _RETRY_ATTEMPTS,
    "AuthenticationErrorRetries": 0,
    "BadRequestErrorRetries": 0,
    "ContentPolicyViolationErrorRetries": 0,
}

Completion = Callable[..., str]


class AIUnavailable(Exception):
    """LiteLLM is missing, unconfigured, or the provider rejected the call."""


def litellm_usable() -> tuple[str, str | None] | None:
    """Model and optional API base when FINDIO_MODEL is set and litellm imports."""
    model = os.environ.get("FINDIO_MODEL", "").strip()
    if not model:
        return None
    api_base = os.environ.get("FINDIO_API_BASE", "").strip() or None
    try:
        import litellm  # noqa: F401
    except ImportError:
        return None
    return model, api_base


def prompt_for(symbols: list[Symbol]) -> str:
    language = symbols[0].language
    rows = "\n".join(f"{symbol.module}\t{symbol.symbol}" for symbol in symbols)
    return (
        "Classify each symbol from this language as I/O or not.\n"
        f"Language: {language}\n"
        "Each line is a module, then a tab, then a symbol. "
        "An empty symbol means the whole module. An empty module is a global or command.\n"
        f"{rows}\n\n"
        "I/O means the API or command is likely to read or write files, use the network, "
        "start a process, talk to a database, or call a specific external service. "
        "In-memory computation, math, and data-structure operations are not I/O. "
        "When unsure whether it is I/O, set io to true. "
        "When unsure which kind applies, include each kind that might. "
        "An incomplete kind list is fine.\n"
        'kinds is a subset of ["filesystem", "network", "service", "process", "database"] '
        "and is empty when io is false.\n"
        "`network` is generic network I/O, such as sockets, HTTP, DNS, or curl. "
        "`service` is a client for one named external service, such as AWS S3, "
        "Google Cloud Storage, or a LiteLLM proxy. Use `service` instead of `network` "
        "for that client. Do not use `service` for an operating-system command that "
        "configures the local network stack, firewall, DNS client, adapters, or CIM. "
        "That command is `network` when it does network I/O. "
        "Console output and a build runner, such as Write-Host or Invoke-Build, are not I/O.\n"
        "Reply with JSON only, covering every symbol:\n"
        '{"results":[{"module":"","symbol":"","io":true,"kinds":["network"]}]}\n'
    )


def parse_response(text: str, batch: list[Symbol]) -> dict[tuple[str, str], Label] | None:
    """Labels for every symbol in the batch, or None when the reply cannot be used."""
    results = _results(text)
    if results is None:
        return None
    return _assign(_rows(results), batch)


def response_problem(text: str, batch: list[Symbol]) -> str:
    """Why ``parse_response`` rejected this reply, for a stderr line."""
    if not text.strip():
        return "empty reply"
    results = _results(text)
    if results is None:
        return "not JSON"
    rows = _rows(results)
    found = _assign(rows, batch)
    if found is not None:
        return "not JSON"
    missing = [symbol for symbol in batch if (symbol.module, symbol.symbol) not in {key for key, _label in rows}]
    if not missing:
        missing = list(batch)
    shown = ", ".join(_short_name(symbol) for symbol in missing[:3])
    if len(missing) > 3:
        shown = f"{shown}, and {len(missing) - 3} more"
    problem = f"missing {shown}"
    exact = {key for key, _label in rows}
    if not any((symbol.module, symbol.symbol) in exact for symbol in batch):
        return f"{problem} ({_sample(results)})"
    return problem


def _results(text: str) -> list[object] | None:
    try:
        data = json.loads(_json_text(text))
        results = data["results"]
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return None
    if not isinstance(results, list):
        return None
    return results


def _rows(results: list[object]) -> list[tuple[tuple[str, str], Label]]:
    rows: list[tuple[tuple[str, str], Label]] = []
    for item in results:
        parsed = _result(item)
        if parsed is not None:
            rows.append(parsed)
    return rows


def _assign(
    rows: list[tuple[tuple[str, str], Label]],
    batch: list[Symbol],
) -> dict[tuple[str, str], Label] | None:
    exact: dict[tuple[str, str], Label] = {}
    by_name: dict[str, Label] = {}
    names_seen: dict[str, tuple[str, str]] = {}
    ambiguous: set[str] = set()
    for key, label in rows:
        exact.setdefault(key, label)
        name = _joined(key)
        if not name:
            continue
        previous = names_seen.get(name)
        if previous is not None and previous != key:
            ambiguous.add(name)
        names_seen.setdefault(name, key)
        by_name.setdefault(name, label)
    wanted = [(symbol.module, symbol.symbol) for symbol in batch]
    wanted_counts: dict[str, int] = {}
    for key in wanted:
        name = _joined(key)
        wanted_counts[name] = wanted_counts.get(name, 0) + 1
    found: dict[tuple[str, str], Label] = {}
    for key in wanted:
        if key in exact:
            found[key] = exact[key]
            continue
        name = _joined(key)
        if name and name not in ambiguous and wanted_counts[name] == 1 and name in by_name:
            found[key] = by_name[name]
    if set(wanted) <= found.keys():
        return {key: found[key] for key in set(wanted)}
    return None


def _sample(results: list[object]) -> str:
    if not results or not isinstance(results[0], dict):
        return "no results"
    item = results[0]
    module = item.get("module", "")
    symbol = item.get("symbol", "")
    module_text = module if isinstance(module, str) else type(module).__name__
    symbol_text = symbol if isinstance(symbol, str) else type(symbol).__name__
    return f"reply {module_text}\t{symbol_text} io={item.get('io')!r}"


def _short_name(symbol: Symbol) -> str:
    return _joined((symbol.module, symbol.symbol))


def _joined(key: tuple[str, str]) -> str:
    module, symbol = key
    if module and symbol:
        return f"{module}.{symbol}"
    return symbol or module


def _sent_retry_policy(retry_policy: dict[str, int] | None) -> dict[str, int]:
    """The policy sent to LiteLLM. Rate limits are waited out here."""
    policy = dict(_RETRY_POLICY if retry_policy is None else retry_policy)
    policy["RateLimitErrorRetries"] = 0
    return policy


def _rate_limited(exc: BaseException) -> bool:
    return type(exc).__name__ == "RateLimitError" or getattr(exc, "status_code", None) == 429


def _wait_seconds(exc: BaseException, attempt: int) -> int:
    """Whole seconds to wait, at least the delay the provider asked for."""
    suggested = _suggested_delay(exc)
    if suggested is None:
        suggested = min(2**attempt, 10)
    return max(1, math.ceil(suggested))


def _suggested_delay(exc: BaseException) -> float | None:
    text = str(exc)
    found = [float(match) for match in _RETRY_IN.findall(text)]
    found.extend(float(match) for match in _RETRY_DELAY.findall(text))
    header = _retry_after(exc)
    if header is not None:
        found.append(header)
    if not found:
        return None
    return max(found)


def _retry_after(exc: BaseException) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        headers = getattr(exc, "headers", None)
    if not hasattr(headers, "get"):
        return None
    raw = headers.get("retry-after")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def call_litellm(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    api_base: str | None,
    max_retries: int = 0,
    retry_strategy: str = "exponential_backoff_retry",
    retry_policy: dict[str, int] | None = None,
) -> str:
    import litellm

    # Provider deprecations, such as Gemini sampling parameters, are warnings.
    for name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy"):
        logging.getLogger(name).setLevel(logging.ERROR)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "timeout": COMPLETION_TIMEOUT,
        "max_retries": max_retries,
        "retry_strategy": retry_strategy,
        "retry_policy": _sent_retry_policy(retry_policy),
    }
    if api_base:
        kwargs["api_base"] = api_base
    if model.startswith("gemini/"):
        kwargs["service_tier"] = "flex"
    for attempt in range(_RETRY_ATTEMPTS + 1):
        try:
            response = litellm.completion(**kwargs)
        except Exception as exc:
            if not _rate_limited(exc) or attempt == _RETRY_ATTEMPTS:
                raise
            seconds = _wait_seconds(exc, attempt)
            print(f"findio: rate limited, retrying in {seconds}s", file=sys.stderr, flush=True)
            time.sleep(seconds)
            continue
        content = response.choices[0].message.content
        return content or ""
    raise RuntimeError("rate limit retries exhausted")


class AIClassifier:
    """One LiteLLM completion for one batch. Tests pass ``complete``."""

    def __init__(self, model: str, api_base: str | None = None, *, complete: Completion | None = None) -> None:
        self.model = model
        self.api_base = api_base or None
        self._complete = complete or call_litellm

    def ask(self, symbols: list[Symbol]) -> str:
        try:
            return self._complete(
                model=self.model,
                messages=[{"role": "user", "content": prompt_for(symbols)}],
                temperature=0,
                api_base=self.api_base,
                max_retries=0,
                retry_strategy="exponential_backoff_retry",
                retry_policy=_RETRY_POLICY,
            )
        except Exception as exc:
            raise AIUnavailable(str(exc)) from exc


def _json_text(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        if "```" in stripped:
            stripped = stripped[: stripped.rfind("```")]
    return stripped.strip()


def _result(item: object) -> tuple[tuple[str, str], Label] | None:
    if not isinstance(item, dict):
        return None
    module = item.get("module")
    symbol = item.get("symbol")
    io = _io_flag(item.get("io"))
    if not isinstance(module, str) or not isinstance(symbol, str) or io is None:
        return None
    kinds = item.get("kinds", [])
    if isinstance(kinds, str):
        kinds = [part.strip() for part in kinds.split(",") if part.strip()]
    if not isinstance(kinds, list):
        return None
    ordered = tuple(kind for kind in _KINDS if kind in kinds)
    if not io:
        ordered = ()
    return (module, symbol), Label(io, ordered)


def _io_flag(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    return None
