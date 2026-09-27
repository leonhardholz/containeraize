"""Cache and LiteLLM classification, with a stubbed completion."""

from __future__ import annotations

import json
import logging
import sys
from types import SimpleNamespace

import pytest

from findio import Symbol, scan
from findio.ai import PROMPT_VERSION, AIClassifier, AIUnavailable, call_litellm, litellm_usable, parse_response, response_problem
from findio.cache import ClassificationCache
from findio.classify import CascadingClassifier
from findio.cli import main
from findio.report import Label


class FakeAI:
    def __init__(self, reply) -> None:
        self.model = "test/model"
        self.calls: list[list[Symbol]] = []
        self.events: list[tuple[str, int]] = []
        self.reply = reply

    def ask(self, symbols: list[Symbol]) -> str:
        self.events.append(("start", len(symbols)))
        self.calls.append(list(symbols))
        try:
            return self.reply(symbols)
        finally:
            self.events.append(("end", len(symbols)))


def _labeled(symbols: list[Symbol], *, io: bool = True, kinds: list[str] | None = None) -> str:
    chosen = ["network"] if kinds is None else kinds
    return json.dumps(
        {
            "results": [
                {"module": symbol.module, "symbol": symbol.symbol, "io": io, "kinds": chosen}
                for symbol in symbols
            ]
        }
    )


def test_static_hit_skips_cache_and_model(tmp_path) -> None:
    ai = FakeAI(lambda symbols: (_ for _ in ()).throw(AssertionError("model was called")))
    cache = ClassificationCache(tmp_path / "classifications.sqlite")

    labels = CascadingClassifier(cache, ai).classify([Symbol("python", "", "open")])

    assert labels == [Label(True, ("filesystem",))]
    assert ai.calls == []
    assert cache.get(Symbol("python", "", "open"), model=ai.model, prompt_version=PROMPT_VERSION) is None


def test_cache_hit_is_not_sent_again(tmp_path) -> None:
    ai = FakeAI(_labeled)
    cache = ClassificationCache(tmp_path / "classifications.sqlite")
    classifier = CascadingClassifier(cache, ai)
    first = [Symbol("python", "acme", "one"), Symbol("python", "acme", "two")]

    assert classifier.classify(first) == [Label(True, ("network",)), Label(True, ("network",))]
    assert classifier.classify([first[0], Symbol("python", "acme", "three")]) == [
        Label(True, ("network",)),
        Label(True, ("network",)),
    ]

    assert [[symbol.symbol for symbol in call] for call in ai.calls] == [["one", "two"], ["three"]]


def test_refresh_asks_again_and_overwrites(tmp_path) -> None:
    answers = iter((True, False))

    def reply(symbols: list[Symbol]) -> str:
        return _labeled(symbols, io=next(answers), kinds=[])

    ai = FakeAI(reply)
    cache = ClassificationCache(tmp_path / "classifications.sqlite")
    symbol = Symbol("python", "acme", "fetch")

    assert CascadingClassifier(cache, ai).classify([symbol]) == [Label(True, ())]
    assert CascadingClassifier(cache, ai, refresh=True).classify([symbol]) == [Label(False, ())]
    assert cache.get(symbol, model=ai.model, prompt_version=PROMPT_VERSION) == Label(False, ())


def test_batches_are_one_language_and_one_call_at_a_time(tmp_path, capsys) -> None:
    ai = FakeAI(_labeled)
    symbols = [Symbol("python", "acme", f"s{index:02d}") for index in range(31)]
    symbols.append(Symbol("go", "acme", "dial"))

    labels = CascadingClassifier(ClassificationCache(tmp_path / "c.sqlite"), ai).classify(symbols)

    assert labels == [Label(True, ("network",))] * 32
    assert [len(call) for call in ai.calls] == [1, 30, 1]
    assert ai.calls[0][0].language == "go"
    assert {symbol.language for symbol in ai.calls[1]} == {"python"}
    assert ai.events == [("start", 1), ("end", 1), ("start", 30), ("end", 30), ("start", 1), ("end", 1)]
    assert capsys.readouterr().err.splitlines() == [
        "classify go 1 acme.dial",
        "classify python 30 " + " ".join(f"acme.s{index:02d}" for index in range(30)),
        "classify python 1 acme.s30",
    ]


def test_invalid_json_splits_until_one_symbol(tmp_path, capsys) -> None:
    def reply(symbols: list[Symbol]) -> str:
        if len(symbols) > 1:
            return "not json"
        return _labeled(symbols)

    ai = FakeAI(reply)
    symbols = [Symbol("python", "acme", "a"), Symbol("python", "acme", "b")]

    labels = CascadingClassifier(ClassificationCache(tmp_path / "c.sqlite"), ai).classify(symbols)

    assert labels == [Label(True, ("network",)), Label(True, ("network",))]
    assert [[symbol.symbol for symbol in call] for call in ai.calls] == [["a", "b"], ["a"], ["b"]]
    assert capsys.readouterr().err.splitlines() == [
        "classify python 2 acme.a acme.b",
        "findio: classification reply could not be used (not JSON); splitting 2 python symbols",
        "classify python 1 acme.a",
        "classify python 1 acme.b",
    ]


def test_unreadable_single_symbol_is_not_io_and_is_not_cached(tmp_path, capsys) -> None:
    ai = FakeAI(lambda _symbols: "not json")
    cache = ClassificationCache(tmp_path / "c.sqlite")
    symbol = Symbol("python", "acme", "fetch")

    assert CascadingClassifier(cache, ai).classify([symbol]) == [Label(False, ())]
    assert cache.get(symbol, model=ai.model, prompt_version=PROMPT_VERSION) is None
    assert "classification failed for acme.fetch (not JSON)" in capsys.readouterr().err


def test_provider_failure_keeps_static_results(tmp_path, capsys) -> None:
    def reply(symbols: list[Symbol]) -> str:
        raise AIUnavailable("no key")

    ai = FakeAI(reply)
    cache = ClassificationCache(tmp_path / "c.sqlite")
    symbols = [Symbol("python", "", "open"), *[Symbol("python", "acme", f"s{index}") for index in range(31)]]

    labels = CascadingClassifier(cache, ai).classify(symbols)

    assert labels[0] == Label(True, ("filesystem",))
    assert labels[1:] == [Label(False, ())] * 31
    assert len(ai.calls) == 1
    assert "LiteLLM is unavailable" in capsys.readouterr().err
    assert cache.get(symbols[1], model=ai.model, prompt_version=PROMPT_VERSION) is None


def test_service_kind_is_kept() -> None:
    symbol = Symbol("python", "boto3", "client")

    parsed = parse_response(_labeled([symbol], kinds=["service", "network"]), [symbol])

    assert parsed == {("boto3", "client"): Label(True, ("network", "service"))}


def test_fenced_json_is_accepted(tmp_path) -> None:
    def reply(symbols: list[Symbol]) -> str:
        return "```json\n" + _labeled(symbols, io=False, kinds=["network", "nope"]) + "\n```"

    ai = FakeAI(reply)
    symbol = Symbol("python", "acme", "fetch")
    cache = ClassificationCache(tmp_path / "c.sqlite")

    assert CascadingClassifier(cache, ai).classify([symbol]) == [Label(False, ())]
    assert cache.get(symbol, model=ai.model, prompt_version=PROMPT_VERSION) == Label(False, ())


def test_string_io_and_joined_name_are_accepted() -> None:
    method = Symbol("java", "org.junit.Assert", "assertTrue")
    whole = Symbol("java", "org.junit.jupiter.api.Test", "")
    reply = json.dumps(
        {
            "results": [
                {"module": "", "symbol": "org.junit.Assert.assertTrue", "io": "false", "kinds": ""},
                {"module": "org.junit.jupiter.api.Test", "symbol": "", "io": "false", "kinds": []},
            ]
        }
    )

    assert parse_response(reply, [method, whole]) == {
        ("org.junit.Assert", "assertTrue"): Label(False, ()),
        ("org.junit.jupiter.api.Test", ""): Label(False, ()),
    }


def test_joined_name_is_not_shared_by_two_symbols() -> None:
    symbols = [
        Symbol("java", "org.junit.Assert", "assertTrue"),
        Symbol("java", "org.junit.Assert.assertTrue", ""),
    ]
    reply = json.dumps({"results": [{"module": "", "symbol": "org.junit.Assert.assertTrue", "io": False, "kinds": []}]})

    assert parse_response(reply, symbols) is None


def test_response_problem_names_missing_symbols() -> None:
    symbols = [Symbol("java", "org.junit.Assert", "assertTrue"), Symbol("java", "org.junit.Assert", "fail")]
    reply = json.dumps({"results": [{"module": "org.junit.Assert", "symbol": "assertTrue", "io": False, "kinds": []}]})

    assert parse_response(reply, symbols) is None
    assert response_problem(reply, symbols) == "missing org.junit.Assert.fail"
    other = json.dumps({"results": [{"module": "junit", "symbol": "Assert", "io": False, "kinds": []}]})
    assert response_problem(other, symbols[:1]).startswith("missing org.junit.Assert.assertTrue (reply junit\tAssert")
    assert response_problem("", symbols) == "empty reply"
    assert response_problem("nope", symbols) == "not JSON"


def test_completion_omits_api_base_unless_set(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def completion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))])

    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(completion=completion))

    assert call_litellm(model="openai/gpt-4o", messages=[{"role": "user", "content": "hi"}], temperature=0, api_base=None) == "{}"
    assert captured["model"] == "openai/gpt-4o"
    assert captured["temperature"] == 0
    assert captured["timeout"] == 60
    assert captured["max_retries"] == 0
    assert captured["retry_strategy"] == "exponential_backoff_retry"
    assert captured["retry_policy"]["RateLimitErrorRetries"] == 0
    assert captured["retry_policy"]["ServiceUnavailableErrorRetries"] == 3
    assert captured["retry_policy"]["AuthenticationErrorRetries"] == 0
    assert "api_base" not in captured
    assert logging.getLogger("LiteLLM").level == logging.ERROR

    call_litellm(
        model="openai/gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0,
        api_base="http://localhost:11434",
    )
    assert captured["api_base"] == "http://localhost:11434"


def test_rate_limit_waits_for_the_suggested_delay(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    class RateLimitError(Exception):
        status_code = 429

    calls = 0

    def completion(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RateLimitError('Please retry in 42.014736441s. "retryDelay": "42s"')
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))])

    slept: list[int] = []
    monkeypatch.setattr("findio.ai.time.sleep", lambda seconds: slept.append(seconds))
    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(completion=completion))

    assert call_litellm(model="gemini/gemini-3.5-flash-lite", messages=[{"role": "user", "content": "hi"}], temperature=0, api_base=None) == "{}"
    assert slept == [43]
    assert "findio: rate limited, retrying in 43s" in capsys.readouterr().err


def test_ai_classifier_sends_the_prompt_and_wraps_provider_errors() -> None:
    seen: dict[str, object] = {}

    def complete(**kwargs) -> str:
        seen.update(kwargs)
        return "{}"

    AIClassifier("ollama/llama3", complete=complete).ask([Symbol("python", "acme", "fetch")])

    assert seen["model"] == "ollama/llama3"
    assert seen["api_base"] is None
    assert seen["temperature"] == 0
    assert seen["retry_strategy"] == "exponential_backoff_retry"
    assert seen["retry_policy"]["TimeoutErrorRetries"] == 3
    assert "acme\tfetch" in seen["messages"][0]["content"]

    def fail(**_kwargs) -> str:
        raise RuntimeError("connection refused")

    with pytest.raises(AIUnavailable, match="connection refused"):
        AIClassifier("ollama/llama3", "http://localhost:11434", complete=fail).ask(
            [Symbol("python", "acme", "fetch")]
        )


def test_missing_model_or_litellm_is_not_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FINDIO_MODEL", raising=False)
    assert litellm_usable() is None

    monkeypatch.setenv("FINDIO_MODEL", "openai/gpt-4o")
    monkeypatch.setenv("FINDIO_API_BASE", "http://localhost:11434")
    monkeypatch.setitem(sys.modules, "litellm", None)
    assert litellm_usable() is None


def test_cli_without_a_usable_model_stays_static(tmp_path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("FINDIO_MODEL", "openai/gpt-4o")
    monkeypatch.setitem(sys.modules, "litellm", None)
    (tmp_path / "main.py").write_text("import json\njson.loads('{}')\nopen('a')\n", encoding="utf-8")

    assert main([str(tmp_path), "--jobs", "1"]) == 0

    assert capsys.readouterr().out == "main.py\n  filesystem  python  open  3\n"
    assert scan(tmp_path).symbols[0].symbol == "open"
