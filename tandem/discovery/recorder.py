"""Durable discovery trace and evidence recording."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from tandem.discovery.provider import BrowserObservation, DiscoveryDecision
from tandem.security.evidence import redact_text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_SENSITIVE = ("api_key", "apikey", "password", "secret", "token", "authorization")


def redact_secrets(value: Any) -> Any:
    """Recursively redact values whose keys commonly contain credentials."""

    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if any(part in key.lower() for part in _SENSITIVE)
                else redact_secrets(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


class ActionTrace(BaseModel):
    """A browser interaction actually executed during discovery."""

    step_id: str
    action: str
    semantic_target: str
    selector: str
    locator_candidates: list[str] = Field(default_factory=list)
    frame_selector: str | None = None
    input_name: str | None = None
    concrete_value: str | None = None
    container_selector: str | None = None
    observed_text: str | None = None
    is_mutating: bool = False
    guard_ref: str | None = None
    timestamp: str = Field(default_factory=_now)


class DiscoveryEvent(BaseModel):
    """One durable model-observe-decide-act cycle."""

    cycle: int
    timestamp: str = Field(default_factory=_now)
    browser_observation: BrowserObservation
    model_decision: DiscoveryDecision
    executed_action: ActionTrace | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    evidence_reference: str | None = None
    observation_reference: str | None = None


class DiscoveryTrace(BaseModel):
    """Complete provider-backed discovery session ready for compilation."""

    run_id: str
    provider: str
    model: str
    started_at: str
    completed_at: str | None = None
    evidence_directory: str | None = None
    capability_id: str
    version: str = "1.0.0"
    system: str = "core_bank"
    goal: str
    actions: list[ActionTrace] = Field(default_factory=list)
    events: list[DiscoveryEvent] = Field(default_factory=list)
    discovered_memo: str | None = None
    money_moved: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceRecorder:
    """Persist model decisions, browser observations, actions, and screenshots."""

    def __init__(
        self,
        capability_id: str,
        goal: str,
        system: str = "core_bank",
        provider: str = "manual",
        model: str = "manual",
        evidence_root: str | Path | None = None,
        run_id: str | None = None,
    ) -> None:
        self.capability_id = capability_id
        self.goal = goal
        self.system = system
        self.provider = provider
        self.model = model
        self.run_id = run_id or str(uuid4())
        self.started_at = _now()
        self.actions: list[ActionTrace] = []
        self.events: list[DiscoveryEvent] = []
        self._step_counter = 0
        self.evidence_directory = (
            Path(evidence_root) / self.run_id if evidence_root is not None else None
        )
        if self.evidence_directory is not None:
            (self.evidence_directory / "screenshots").mkdir(parents=True, exist_ok=True)
            (self.evidence_directory / "observations").mkdir(parents=True, exist_ok=True)

    def record_navigate(self, url: str, semantic_target: str | None = None) -> ActionTrace:
        self._step_counter += 1
        trace = ActionTrace(
            step_id=f"step_{self._step_counter}_nav",
            action="NAVIGATE",
            semantic_target=semantic_target or f"surface://{self.system}/home",
            selector=url,
            concrete_value=url,
        )
        self.actions.append(trace)
        return trace

    def record_fill(
        self,
        selector: str,
        value: str,
        input_name: str | None = None,
        semantic_target: str | None = None,
        frame_selector: str | None = None,
        locator_candidates: list[str] | None = None,
    ) -> ActionTrace:
        self._step_counter += 1
        candidates = list(locator_candidates or [selector])
        if selector not in candidates:
            candidates.insert(0, selector)
        trace = ActionTrace(
            step_id=f"step_{self._step_counter}_fill_{input_name or 'input'}",
            action="FILL",
            semantic_target=semantic_target or f"Input field for {input_name or selector}",
            selector=selector,
            locator_candidates=candidates,
            frame_selector=frame_selector,
            input_name=input_name,
            concrete_value=str(value),
        )
        self.actions.append(trace)
        return trace

    def record_click(
        self,
        selector: str,
        semantic_target: str | None = None,
        frame_selector: str | None = None,
        locator_candidates: list[str] | None = None,
        container_selector: str | None = None,
        observed_text: str | None = None,
        is_mutating: bool = False,
        guard_ref: str | None = None,
    ) -> ActionTrace:
        self._step_counter += 1
        candidates = list(locator_candidates or [selector])
        if selector not in candidates:
            candidates.insert(0, selector)
        trace = ActionTrace(
            step_id=f"step_{self._step_counter}_click",
            action="SUBMIT" if is_mutating else "CLICK",
            semantic_target=semantic_target or f"Click target {selector}",
            selector=selector,
            locator_candidates=candidates,
            frame_selector=frame_selector,
            container_selector=container_selector,
            observed_text=observed_text,
            is_mutating=is_mutating,
            guard_ref=guard_ref,
        )
        self.actions.append(trace)
        return trace

    def record_cycle(
        self,
        observation: BrowserObservation,
        decision: DiscoveryDecision,
        executed_action: ActionTrace | None,
        result: dict[str, Any] | None = None,
        screenshot: bytes | None = None,
    ) -> DiscoveryEvent:
        cycle = len(self.events) + 1
        screenshot_ref = None
        observation_ref = None
        if self.evidence_directory is not None:
            observation_ref = f"observations/cycle_{cycle:03d}.json"
            (self.evidence_directory / observation_ref).write_text(
                json.dumps(
                    redact_secrets(observation.model_dump(mode="json")),
                    sort_keys=True,
                    indent=2,
                ),
                encoding="utf-8",
            )
            if screenshot is not None:
                screenshot_ref = f"screenshots/cycle_{cycle:03d}.png"
                (self.evidence_directory / screenshot_ref).write_bytes(screenshot)
        event = DiscoveryEvent(
            cycle=cycle,
            browser_observation=observation,
            model_decision=decision,
            executed_action=executed_action,
            result=redact_secrets(result or {}),
            evidence_reference=screenshot_ref,
            observation_reference=observation_ref,
        )
        self.events.append(event)
        self._write_trace(self._build_trace(completed_at=None))
        return event

    def finalize(
        self,
        discovered_memo: str | None = None,
        money_moved: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> DiscoveryTrace:
        trace = self._build_trace(
            completed_at=_now(),
            discovered_memo=discovered_memo,
            money_moved=money_moved,
            metadata=redact_secrets(metadata or {}),
        )
        self._write_trace(trace)
        return trace

    def _build_trace(
        self,
        completed_at: str | None,
        discovered_memo: str | None = None,
        money_moved: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> DiscoveryTrace:
        return DiscoveryTrace(
            run_id=self.run_id,
            provider=self.provider,
            model=self.model,
            started_at=self.started_at,
            completed_at=completed_at,
            evidence_directory=(
                str(self.evidence_directory.resolve()) if self.evidence_directory else None
            ),
            capability_id=self.capability_id,
            system=self.system,
            goal=self.goal,
            actions=list(self.actions),
            events=list(self.events),
            discovered_memo=discovered_memo,
            money_moved=money_moved,
            metadata=metadata or {},
        )

    def _write_trace(self, trace: DiscoveryTrace) -> None:
        if self.evidence_directory is None:
            return
        payload = redact_secrets(trace.model_dump(mode="json"))
        (self.evidence_directory / "trace.json").write_text(
            json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8"
        )
