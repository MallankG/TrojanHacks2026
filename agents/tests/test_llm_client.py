import json
import logging
import os
from collections.abc import Callable

import httpx
import pytest

from tracelayer_agents.llm.client import LLMClient, LLMSettings
from tracelayer_agents.llm.templates import EchoOutput

SECRET = "acct 4417-PRIVATE wire to Jane Q. Example"
MODEL = "test-model:1b"
Handler = Callable[[httpx.Request], httpx.Response]


def _tags(*names: str) -> httpx.Response:
    return httpx.Response(200, json={"models": [{"name": n, "model": n} for n in names]})


def _chat_ok(content: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})


def _ollama(chat: Handler, tags: Handler = lambda r: _tags(MODEL)) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return tags(request)
        if request.url.path == "/api/chat":
            return chat(request)
        return httpx.Response(404)

    return handler


def _client(handler: Handler, *, model: str | None = MODEL, cap: int = 6) -> LLMClient:
    settings = LLMSettings(provider="ollama", ollama_model=model, max_calls_per_run=cap)
    return LLMClient(settings, transport=httpx.MockTransport(handler))


async def _echo(client: LLMClient, trace_id: str = "tr_1"):
    return await client.generate_structured(
        "echo", {"message": SECRET}, EchoOutput, trace_id=trace_id
    )


def _unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


# --------------------------------------------------------------------------- happy path


async def test_ollama_ok_returns_parsed_output() -> None:
    seen: list[dict] = []

    def chat(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _chat_ok(json.dumps({"text": "hello"}))

    out, meta = await _echo(_client(_ollama(chat)))

    assert out == EchoOutput(text="hello")
    assert (meta.provider, meta.model, meta.fallback_used) == ("ollama", MODEL, False)
    assert meta.prompt_version == "echo_v1"
    assert meta.attempts == 1 and meta.errors == []
    body = seen[0]
    assert body["model"] == MODEL and body["stream"] is False and body["think"] is False
    assert body["format"] == EchoOutput.model_json_schema()
    assert body["options"] == {"temperature": 0.1, "num_predict": 1200}
    assert SECRET in body["messages"][-1]["content"]


async def test_unset_model_uses_first_from_tags(caplog: pytest.LogCaptureFixture) -> None:
    seen: list[str] = []

    def chat(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["model"])
        return _chat_ok(json.dumps({"text": "hi"}))

    caplog.set_level(logging.INFO, logger="tracelayer_agents")
    client = _client(_ollama(chat, tags=lambda r: _tags("first:7b", "second:3b")), model=None)
    _, meta = await _echo(client)

    assert seen == ["first:7b"]
    assert meta.provider == "ollama" and meta.model == "first:7b"
    assert "first:7b" in caplog.text


async def test_think_rejected_retries_without_it() -> None:
    bodies: list[dict] = []

    def chat(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "think" in body:
            return httpx.Response(400, json={"error": f'"{MODEL}" does not support thinking'})
        return _chat_ok(json.dumps({"text": "ok"}))

    client = _client(_ollama(chat))
    out, meta = await _echo(client)
    assert out.text == "ok" and meta.provider == "ollama" and meta.attempts == 2
    assert "think" in bodies[0] and "think" not in bodies[1]

    # remembered per process: the next call goes straight to the no-think request
    await _echo(client, trace_id="tr_2")
    assert "think" not in bodies[2] and len(bodies) == 3


async def test_template_provider_never_calls_ollama() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("network used")

    client = LLMClient(LLMSettings(provider="template"), transport=httpx.MockTransport(handler))
    out, meta = await _echo(client)
    assert out == EchoOutput(text=SECRET)
    assert (meta.provider, meta.model, meta.attempts, meta.fallback_used) == (
        "template",
        "template",
        0,
        True,
    )


# --------------------------------------------------------------------------- fallbacks


async def test_unreachable_falls_to_template_and_fails_fast() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return _unreachable(request)

    client = _client(handler)
    out, meta = await _echo(client)
    assert out == EchoOutput(text=SECRET)
    assert meta.provider == "template" and meta.errors == ["ollama_unreachable"]
    assert meta.attempts == 0

    await _echo(client, trace_id="tr_2")
    assert calls == ["/api/tags"]  # health result cached per process


async def test_timeout_falls_to_template() -> None:
    def chat(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    out, meta = await _echo(_client(_ollama(chat)))
    assert out.text == SECRET and meta.errors == ["ollama_timeout"]


async def test_server_error_falls_to_template() -> None:
    out, meta = await _echo(_client(_ollama(lambda r: httpx.Response(503))))
    assert out.text == SECRET and meta.errors == ["ollama_http_503"]


async def test_invalid_json_twice_falls_to_template() -> None:
    prompts: list[str] = []

    def chat(request: httpx.Request) -> httpx.Response:
        prompts.append(json.loads(request.content)["messages"][-1]["content"])
        return _chat_ok("not json {")

    out, meta = await _echo(_client(_ollama(chat)))
    assert out == EchoOutput(text=SECRET)
    assert meta.provider == "template" and meta.attempts == 2
    assert meta.errors == ["invalid_output_attempt_1", "invalid_output_attempt_2"]
    assert "failed validation" not in prompts[0] and "failed validation" in prompts[1]


async def test_invalid_then_valid_uses_retry() -> None:
    replies = iter(["{}", json.dumps({"text": "fixed"})])
    out, meta = await _echo(_client(_ollama(lambda r: _chat_ok(next(replies)))))
    assert out.text == "fixed" and meta.provider == "ollama" and meta.attempts == 2
    assert meta.errors == ["invalid_output_attempt_1"]


async def test_model_not_found_falls_to_template() -> None:
    def chat(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": f'model "{MODEL}" not found, try pulling it'})

    out, meta = await _echo(_client(_ollama(chat)))
    assert out.text == SECRET and meta.errors == ["ollama_model_not_found"]


async def test_no_local_models_falls_to_template() -> None:
    def chat(request: httpx.Request) -> httpx.Response:
        raise AssertionError("chat called without a model")

    out, meta = await _echo(_client(_ollama(chat, tags=lambda r: _tags()), model=None))
    assert out.text == SECRET
    assert meta.provider == "template" and meta.errors == ["ollama_no_models"]
    assert meta.model == "template"


async def test_call_cap_per_trace_falls_to_template() -> None:
    client = _client(_ollama(lambda r: _chat_ok(json.dumps({"text": "llm"}))), cap=2)

    assert (await _echo(client))[1].provider == "ollama"
    assert (await _echo(client))[1].provider == "ollama"
    out, meta = await _echo(client)
    assert out.text == SECRET and meta.errors == ["llm_call_cap_reached"]
    assert (await _echo(client, trace_id="tr_other"))[1].provider == "ollama"


# --------------------------------------------------------------------------- invariants

SCENARIOS: dict[str, Handler] = {
    "ok": _ollama(lambda r: _chat_ok(json.dumps({"text": "x"}))),
    "unreachable": _unreachable,
    "invalid_json": _ollama(lambda r: _chat_ok("nope")),
    "model_not_found": _ollama(lambda r: httpx.Response(404, json={"error": "not found"})),
    "server_error": _ollama(lambda r: httpx.Response(500)),
}


@pytest.mark.parametrize("scenario", SCENARIOS)
async def test_same_schema_and_no_prompt_in_logs(
    scenario: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    out, meta = await _echo(_client(SCENARIOS[scenario]))

    assert type(out) is EchoOutput
    assert len(meta.input_hash) == 64
    assert SECRET not in caplog.text
    assert "Return a JSON object" not in caplog.text  # prompt template text
    assert all(SECRET not in e for e in meta.errors)
    assert meta.input_hash in caplog.text


async def test_input_hash_is_stable_and_input_sensitive() -> None:
    client = LLMClient(LLMSettings(provider="template"))
    _, a = await client.generate_structured("echo", {"message": "a"}, EchoOutput, trace_id="t")
    _, b = await client.generate_structured("echo", {"message": "a"}, EchoOutput, trace_id="t")
    _, c = await client.generate_structured("echo", {"message": "b"}, EchoOutput, trace_id="t")
    assert a.input_hash == b.input_hash != c.input_hash


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "LLM_PROVIDER",
        "OLLAMA_HOST",
        "OLLAMA_MODEL",
        "LLM_TIMEOUT_S",
        "LLM_MAX_CALLS_PER_RUN",
    ):
        monkeypatch.delenv(var, raising=False)
    assert LLMSettings.from_env() == LLMSettings()
    assert LLMSettings.from_env().ollama_model is None

    monkeypatch.setenv("LLM_PROVIDER", "bogus")
    with pytest.raises(ValueError):
        LLMSettings.from_env()


# --------------------------------------------------------------------------- opt-in real Ollama


@pytest.mark.ollama
@pytest.mark.skipif(os.environ.get("RUN_OLLAMA_TESTS") != "1", reason="set RUN_OLLAMA_TESTS=1")
async def test_real_local_ollama() -> None:
    settings = LLMSettings.from_env()
    client = LLMClient(LLMSettings(**{**settings.__dict__, "provider": "ollama"}))
    out, meta = await client.generate_structured(
        "echo", {"message": "hello"}, EchoOutput, trace_id="tr_real"
    )
    assert meta.provider == "ollama", meta.errors
    assert isinstance(out, EchoOutput)
