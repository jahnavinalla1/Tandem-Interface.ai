"""Regression contracts for provider-directed discovery and durable evidence."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from tandem.discovery.compiler import CapabilityCompiler
from tandem.discovery.provider import (
    BrowserObservation,
    DiscoveryContext,
    DiscoveryDecision,
    OpenAIResponsesProvider,
)
from tandem.discovery.recorder import TraceRecorder
from tandem.policy.telemetry import llm_tracker


def test_openai_provider_uses_responses_api_and_records_real_invocation() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "action": "FILL",
                                        "semantic_target": "Member lookup",
                                        "selector": "#provider-chosen-selector",
                                        "locator_candidates": ["#provider-chosen-selector"],
                                        "frame_selector": "#workspace",
                                        "input_name": "member_id",
                                        "target_url": None,
                                        "container_selector": None,
                                        "is_mutating": False,
                                        "guard_ref": None,
                                        "rationale": "The observed form exposes a member field.",
                                    }
                                ),
                            }
                        ],
                    }
                ]
            },
        )

    llm_tracker.reset()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAIResponsesProvider(api_key="super-secret", model="gpt-test", client=client)
    decision = provider.decide(
        DiscoveryContext(
            objective="Locate a member",
            inputs={"member_id": "8830142"},
            observation=BrowserObservation(url="https://bank.invalid", title="Bank"),
        )
    )

    assert decision.selector == "#provider-chosen-selector"
    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["authorization"] == "Bearer super-secret"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "gpt-test"
    assert payload["store"] is False
    assert payload["text"]["format"]["type"] == "json_schema"
    assert "Locate a member" in payload["input"]
    assert "8830142" in payload["input"]
    assert llm_tracker.call_count == 1


def test_recorder_persists_sanitized_cycle_evidence(tmp_path: Path) -> None:
    recorder = TraceRecorder(
        capability_id="core.post_provisional_credit",
        goal="Post a credit",
        provider="openai",
        model="gpt-test",
        evidence_root=tmp_path,
        run_id="run-evidence-1",
    )
    action = recorder.record_fill(
        selector="#member-from-provider",
        value="8830142",
        input_name="member_id",
        semantic_target="Member lookup",
    )
    recorder.record_cycle(
        observation=BrowserObservation(
            url="https://bank.invalid",
            title="Bank",
            body_text="Member search",
            interactive_elements=["input#member-from-provider"],
        ),
        decision=DiscoveryDecision(
            action="FILL",
            semantic_target="Member lookup",
            selector="#member-from-provider",
            locator_candidates=["#member-from-provider"],
            input_name="member_id",
            rationale="Observed a labelled member field",
        ),
        executed_action=action,
        result={"status": "executed", "api_key": "must-not-persist"},
        screenshot=b"png-bytes",
    )
    trace = recorder.finalize(metadata={"access_token": "must-not-persist"})

    evidence_dir = Path(trace.evidence_directory)
    assert evidence_dir == tmp_path / "run-evidence-1"
    assert (evidence_dir / "trace.json").exists()
    assert (evidence_dir / "screenshots" / "cycle_001.png").read_bytes() == b"png-bytes"
    assert (evidence_dir / "observations" / "cycle_001.json").exists()
    durable = (evidence_dir / "trace.json").read_text(encoding="utf-8")
    assert "must-not-persist" not in durable
    assert "#member-from-provider" in durable
    assert trace.run_id == "run-evidence-1"
    assert trace.provider == "openai"
    assert trace.model == "gpt-test"
    assert len(trace.events) == 1
    assert trace.events[0].evidence_reference == "screenshots/cycle_001.png"


def test_compiler_is_bound_to_recorded_provider_events(tmp_path: Path) -> None:
    recorder = TraceRecorder(
        capability_id="core.post_provisional_credit",
        goal="Post a credit",
        provider="openai",
        model="gpt-test",
        evidence_root=tmp_path / "evidence",
        run_id="run-compiler-1",
    )
    action = recorder.record_click(
        selector="#model-selected-submit",
        semantic_target="Final external credit actuation",
        locator_candidates=["#model-selected-submit"],
        container_selector="#model-selected-container",
        is_mutating=True,
        guard_ref="primary_commit_guard",
    )
    recorder.record_cycle(
        observation=BrowserObservation(url="https://bank.invalid/confirm"),
        decision=DiscoveryDecision(
            action="SUBMIT",
            semantic_target="Final external credit actuation",
            selector="#model-selected-submit",
            locator_candidates=["#model-selected-submit"],
            container_selector="#model-selected-container",
            is_mutating=True,
            guard_ref="primary_commit_guard",
            rationale="Confirmation form is ready",
        ),
        executed_action=action,
        result={"status": "executed", "receipt_reference": "MC-123"},
    )
    trace = recorder.finalize(discovered_memo="MC-123", money_moved=True)

    capability, _ = CapabilityCompiler(str(tmp_path / "compiled")).compile(trace)

    assert capability.source_discovery_run_id == "run-compiler-1"
    assert capability.created_at == trace.completed_at
    assert capability.supported_surfaces == ["core_bank"]
    assert capability.steps[0].locator_candidates == ["#model-selected-submit"]
    assert capability.scoped_guard is not None
    assert capability.scoped_guard.container_selector == "#model-selected-container"
    assert capability.output_schema["properties"]["receipt_reference"]["type"] == "string"


def test_provider_rejects_malformed_response() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"output": []})
        )
    )
    provider = OpenAIResponsesProvider(api_key="key", model="gpt-test", client=client)
    with pytest.raises(ValueError, match="structured decision"):
        provider.decide(
            DiscoveryContext(
                objective="Do work",
                observation=BrowserObservation(url="about:blank"),
            )
        )
