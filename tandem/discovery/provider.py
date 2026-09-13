"""Typed provider boundary for model-directed browser discovery."""

from __future__ import annotations

from enum import Enum
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from tandem.policy.telemetry import llm_tracker


class DiscoveryAction(str, Enum):
    """Browser actions a discovery provider is allowed to request."""

    NAVIGATE = "NAVIGATE"
    FILL = "FILL"
    CLICK = "CLICK"
    SUBMIT = "SUBMIT"
    FINISH = "FINISH"


class BrowserObservation(BaseModel):
    """Bounded semantic page state sent to the discovery provider."""

    model_config = ConfigDict(extra="forbid")

    url: str
    title: str = ""
    body_text: str = ""
    interactive_elements: list[str] = Field(default_factory=list)
    frame_summaries: list[str] = Field(default_factory=list)


class DiscoveryDecision(BaseModel):
    """Validated action selected by a discovery model."""

    model_config = ConfigDict(extra="forbid")

    action: DiscoveryAction
    semantic_target: str
    selector: str | None = None
    locator_candidates: list[str] = Field(default_factory=list)
    frame_selector: str | None = None
    input_name: str | None = None
    target_url: str | None = None
    container_selector: str | None = None
    is_mutating: bool = False
    guard_ref: str | None = None
    rationale: str

    @model_validator(mode="after")
    def validate_action_shape(self) -> "DiscoveryDecision":
        if self.action == DiscoveryAction.NAVIGATE and not self.target_url:
            raise ValueError("NAVIGATE requires target_url")
        if self.action == DiscoveryAction.FILL and (not self.selector or not self.input_name):
            raise ValueError("FILL requires selector and input_name")
        if self.action in {DiscoveryAction.CLICK, DiscoveryAction.SUBMIT} and not self.selector:
            raise ValueError(f"{self.action.value} requires selector")
        if self.action == DiscoveryAction.SUBMIT:
            if not self.is_mutating or not self.container_selector or not self.guard_ref:
                raise ValueError(
                    "SUBMIT requires is_mutating, container_selector, and guard_ref"
                )
        elif self.is_mutating:
            raise ValueError("Only a SUBMIT discovery action may be marked mutating")
        return self


class DiscoveryContext(BaseModel):
    """Complete decision context for one model/browser cycle."""

    model_config = ConfigDict(extra="forbid")

    objective: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    observation: BrowserObservation
    prior_events: list[dict[str, Any]] = Field(default_factory=list)
    allowed_surfaces: list[str] = Field(default_factory=list)


class DiscoveryProvider(Protocol):
    """A provider that selects the next browser action from observed state."""

    @property
    def provider_name(self) -> str: ...

    @property
    def model(self) -> str: ...

    def decide(self, context: DiscoveryContext) -> DiscoveryDecision: ...


class OpenAIResponsesProvider:
    """OpenAI Responses API adapter using strict structured output."""

    endpoint = "https://api.openai.com/v1/responses"

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-5",
        client: httpx.Client | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OPENAI_API_KEY is required for provider-backed discovery")
        self._api_key = api_key
        self._model = model
        self._client = client or httpx.Client(timeout=timeout_seconds)

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return self._model

    def decide(self, context: DiscoveryContext) -> DiscoveryDecision:
        payload = {
            "model": self.model,
            "store": False,
            "instructions": (
                "Select exactly one safe next browser action from the observed page state. "
                "Never invent selectors absent from the observation. Use input_name rather "
                "than copying input values. Mark only the final externally consequential form "
                "submission as SUBMIT. Return JSON matching the supplied schema."
            ),
            "input": context.model_dump_json(),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "browser_discovery_decision",
                    "schema": self._strict_decision_schema(),
                    "strict": True,
                }
            },
        }
        llm_tracker.record_call(
            model=self.model,
            prompt_snippet=f"Discovery decision for {context.objective}",
            provider=self.provider_name,
        )
        response = self._client.post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        output_text = self._extract_output_text(response.json())
        try:
            return DiscoveryDecision.model_validate_json(output_text)
        except (ValidationError, ValueError) as exc:
            raise ValueError("Provider returned an invalid structured discovery decision") from exc

    @staticmethod
    def _strict_decision_schema() -> dict[str, Any]:
        # Strict structured outputs require every property in required, including
        # nullable fields that Pydantic otherwise represents as optional defaults.
        schema = DiscoveryDecision.model_json_schema()
        schema["required"] = list(schema["properties"])
        for prop in schema["properties"].values():
            prop.pop("default", None)
        return schema

    @staticmethod
    def _extract_output_text(payload: dict[str, Any]) -> str:
        direct = payload.get("output_text")
        if isinstance(direct, str) and direct:
            return direct
        for item in payload.get("output", []):
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if isinstance(content, dict) and content.get("type") == "output_text":
                    text = content.get("text")
                    if isinstance(text, str) and text:
                        return text
        raise ValueError("Provider response did not contain a structured decision")
