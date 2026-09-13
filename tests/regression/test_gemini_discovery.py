"""Offline Gemini HTTP contract tests, not evidence of a live model run."""

import json

import httpx
import pytest

from tandem.config import Settings
from tandem.discovery.factory import create_discovery_provider
from tandem.discovery.gemini import GeminiProvider
from tandem.discovery.provider import BrowserObservation, DiscoveryContext
from tandem.policy.telemetry import llm_tracker


def context():
    return DiscoveryContext(objective="Find the member", inputs={"member_id": "8830142"},
                            observation=BrowserObservation(url="http://127.0.0.1:8001"))


def response_payload(text=None, finish="STOP"):
    decision = dict(action="FILL", semantic_target="Member lookup", selector="#member",
                    input_name="member_id", rationale="The member field is visible")
    return {"candidates": [{"finishReason": finish, "content": {"parts": [
        {"text": "private reasoning", "thought": True},
        {"text": text if text is not None else json.dumps(decision)},
    ]}}]}


def test_gemini_request_and_validated_decision():
    def handler(request):
        assert request.url.path == "/v1beta/models/gemini-3.6-flash:generateContent"
        assert request.headers["x-goog-api-key"] == "test-secret"
        assert "test-secret" not in str(request.url)
        body = json.loads(request.content)
        config = body["generationConfig"]
        assert config["responseMimeType"] == "application/json"
        assert config["responseJsonSchema"]["properties"]["action"]["enum"]
        assert "$ref" not in json.dumps(config["responseJsonSchema"])
        assert "Find the member" in body["contents"][0]["parts"][0]["text"]
        return httpx.Response(200, json=response_payload())

    llm_tracker.reset()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        decision = GeminiProvider("test-secret", client=client).decide(context())
    assert decision.selector == "#member"
    assert decision.input_name == "member_id"
    assert llm_tracker.call_count == 1


@pytest.mark.parametrize("payload", [
    {}, response_payload(finish="MAX_TOKENS"), response_payload(finish="SAFETY"),
    response_payload(text="not json"),
    response_payload(text=json.dumps(dict(action="SUBMIT", selector="#commit",
                                         semantic_target="Commit", rationale="Go"))),
])
def test_gemini_rejects_missing_incomplete_and_unsafe_output(payload):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
        with pytest.raises(ValueError, match="invalid discovery decision"):
            GeminiProvider("test-secret", client=client).decide(context())


@pytest.mark.parametrize("status,message", [(429, "quota/rate limit"), (403, "access denied"),
                                            (401, "access denied"), (400, "HTTP 400"), (500, "HTTP 500")])
def test_gemini_errors_do_not_echo_response_body(status, message):
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(status, text="test-secret raw provider data")
    )) as client:
        with pytest.raises(RuntimeError, match=message) as error:
            GeminiProvider("test-secret", client=client).decide(context())
    assert "test-secret" not in str(error.value)


def test_provider_factory_defaults_and_missing_key(monkeypatch):
    for key in ("DISCOVERY_PROVIDER", "DISCOVERY_MODEL", "GEMINI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    config = Settings(_env_file=None, gemini_api_key="test-secret")
    provider = create_discovery_provider(config)
    assert provider.provider_name == "gemini"
    assert provider.model == "gemini-3.6-flash"
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        create_discovery_provider(Settings(_env_file=None))
    provider = create_discovery_provider(Settings(_env_file=None, discovery_provider="openai",
                                                  openai_api_key="test-secret"))
    assert provider.provider_name == "openai"
    assert provider.model == "gpt-5"


def test_gemini_rejects_wrong_model_and_transport_failure():
    with pytest.raises(ValueError, match="Gemini model ID"):
        GeminiProvider("test-secret", model="gpt-5")

    def timeout(request):
        raise httpx.ReadTimeout("test-secret", request=request)

    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(RuntimeError, match="timed out") as error:
            GeminiProvider("test-secret", client=client).decide(context())
    assert "test-secret" not in str(error.value)


def test_gemini_retries_transient_inference_without_repeating_browser_actions(monkeypatch):
    calls = []
    monkeypatch.setattr('tandem.discovery.gemini.time.sleep', lambda _: None)

    def handler(request):
        calls.append(request)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, json=response_payload())

    llm_tracker.reset()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert GeminiProvider('test-secret', client=client).decide(context()).input_name == 'member_id'
    assert len(calls) == llm_tracker.call_count == 2
