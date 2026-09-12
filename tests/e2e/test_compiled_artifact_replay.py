"""End-to-end test verifying discovery, capability compilation, and zero-LLM replay.

Invariants verified:
1. Provider decisions, rather than hardcoded agent actions, drive discovery.
2. Compiler transforms trace into validated, versioned YAML artifact with SHA-256 integrity hash.
3. Replay of the compiled artifact posts credit successfully.
4. Replay executes with ZERO LLM calls (llm_call_count == 0).
"""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from simulators.core_bank.state import core_bank_state
from tandem.discovery.agent import DiscoveryAgent
from tandem.discovery.compiler import CapabilityCompiler
from tandem.discovery.provider import DiscoveryContext, DiscoveryDecision
from tandem.domain.capability import load_capability_from_yaml
from tandem.domain.outcomes import OutcomeCategory, OutcomeCode
from tandem.policy.telemetry import llm_tracker
from tandem.replay.executor import DeterministicExecutor
from tests.server_utils import ensure_simulators_running, reset_all_simulators


class ScriptedDiscoveryProvider:
    """Deterministic provider substitute for exercising the real decision boundary in CI."""

    provider_name = "scripted-test-provider"
    model = "scripted-browser-model-v1"

    def __init__(self, portal_url: str) -> None:
        self.contexts: list[DiscoveryContext] = []
        common = {"frame_selector": "#core_workspace_frame"}
        self.decisions = [
            DiscoveryDecision(
                action="NAVIGATE",
                semantic_target="Core banking home",
                target_url=portal_url,
                rationale="Open the only allowed banking surface.",
            ),
            DiscoveryDecision(
                action="FILL",
                semantic_target="Member Search Input",
                selector="input[name='q']",
                locator_candidates=["input[name='q']", "#search_input"],
                input_name="member_id",
                rationale="Observed the member search field.",
                **common,
            ),
            DiscoveryDecision(
                action="CLICK",
                semantic_target="Search Button",
                selector="button[type='submit']",
                locator_candidates=["button[type='submit']", "#search_btn"],
                rationale="Submit the member search.",
                **common,
            ),
            DiscoveryDecision(
                action="CLICK",
                semantic_target="Post Provisional Credit Link",
                selector="a.action-credit-btn",
                locator_candidates=["a.action-credit-btn", "text=Post Provisional Credit"],
                rationale="Open the observed credit workflow for the member.",
                **common,
            ),
            DiscoveryDecision(
                action="FILL",
                semantic_target="Case ID Field",
                selector="input[name='case_id']",
                locator_candidates=["input[name='case_id']"],
                input_name="case_id",
                rationale="Bind the case reference.",
                **common,
            ),
            DiscoveryDecision(
                action="FILL",
                semantic_target="Amount Field",
                selector="input[name='amount']",
                locator_candidates=["input[name='amount']"],
                input_name="amount",
                rationale="Bind the typed monetary amount.",
                **common,
            ),
            DiscoveryDecision(
                action="CLICK",
                semantic_target="Proceed to Confirmation",
                selector="button.btn-proceed",
                locator_candidates=["button.btn-proceed", "text=Review & Continue >>"],
                rationale="Open the review panel before submission.",
                **common,
            ),
            DiscoveryDecision(
                action="SUBMIT",
                semantic_target="Final external credit actuation",
                selector="button.btn-commit-final",
                locator_candidates=["button.btn-commit-final", "#btn_commit_credit"],
                container_selector="#credit_action_container, .confirm-panel",
                is_mutating=True,
                guard_ref="primary_commit_guard",
                rationale="The review panel exposes the final mutating submission.",
                **common,
            ),
            DiscoveryDecision(
                action="FINISH",
                semantic_target="Credit receipt",
                rationale="A durable memo receipt is now visible.",
            ),
        ]

    def decide(self, context: DiscoveryContext) -> DiscoveryDecision:
        self.contexts.append(context)
        return self.decisions[len(self.contexts) - 1]


@pytest.fixture(scope="module", autouse=True)
def setup_simulators():
    ensure_simulators_running()


@pytest.fixture(autouse=True)
def reset_test_state():
    reset_all_simulators()
    llm_tracker.reset()


def test_discovery_compilation_and_zero_llm_replay(tmp_path: Path):
    # =======================================================================
    # PHASE 1: Discovery (Model-Driven Exploration)
    # =======================================================================
    llm_tracker.reset()
    assert llm_tracker.call_count == 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        portal_url = "http://127.0.0.1:8001"
        provider = ScriptedDiscoveryProvider(portal_url)
        agent = DiscoveryAgent(
            page=page,
            provider=provider,
            evidence_root=tmp_path / "evidence" / "discovery",
        )
        discovery_inputs = {
            "member_id": "8830142",
            "case_id": "D-DISC-001",
            "amount": 340.00,
        }
        trace = agent.discover_provisional_credit(
            inputs=discovery_inputs,
            portal_url=portal_url,
        )
        browser.close()

    # Verify provider-driven discovery outcomes and durable evidence.
    assert trace.money_moved is True
    assert trace.discovered_memo is not None
    assert trace.discovered_memo.startswith("MC-")
    assert len(trace.actions) >= 5

    assert len(provider.contexts) == len(trace.events) == 9
    assert provider.contexts[0].objective == trace.goal
    assert provider.contexts[0].inputs["member_id"] == "8830142"
    assert any(
        "'name': 'q'" in item for item in provider.contexts[1].observation.interactive_elements
    )
    assert Path(trace.evidence_directory, "trace.json").exists()
    assert trace.provider == provider.provider_name
    assert trace.model == provider.model
    # Scripted CI decisions do not pretend to be model calls. The provider adapter
    # network contract is covered separately with an intercepted Responses request.
    assert llm_tracker.call_count == 0

    # =======================================================================
    # PHASE 2: Compilation (Synthesize Typed, Versioned Capability Artifact)
    # =======================================================================
    compiler = CapabilityCompiler(output_dir=str(tmp_path / "compiled"))
    compiled_cap, artifact_path = compiler.compile(
        trace=trace,
        target_filename="core_post_provisional_credit.yaml",
    )

    assert artifact_path.exists()
    assert compiled_cap.artifact_hash is not None
    assert len(compiled_cap.artifact_hash) == 64

    # Verify compiled artifact is self-contained and valid
    loaded_cap = load_capability_from_yaml(str(artifact_path))
    assert loaded_cap.id == "core.post_provisional_credit"
    assert loaded_cap.effect.effect_class.value == "COMMIT"
    assert loaded_cap.scoped_guard is not None
    assert loaded_cap.scoped_guard.container_selector == "#credit_action_container, .confirm-panel"

    # =======================================================================
    # PHASE 3: Replay Compiled Artifact on New Case (Zero-LLM Invariant)
    # =======================================================================
    # Reset external state and LLM counter
    reset_all_simulators()
    llm_tracker.reset()
    assert llm_tracker.call_count == 0

    # Execute deterministic replay of the compiled artifact
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        executor = DeterministicExecutor(page=page)
        replay_inputs = {
            "institution_id": "alpha",
            "member_id": "8830142",
            "account_id": "CHK-8830142-01",
            "case_id": "D-REPLAY-9901",
            "amount": 340.00,
            "currency": "USD",
        }
        outcome = executor.execute(capability=loaded_cap, inputs=replay_inputs)
        browser.close()

    # Verify successful execution of the compiled artifact
    assert outcome.category == OutcomeCategory.SUCCESS, (
        f"Replay failed: {outcome.code} - {outcome.message}"
    )
    assert outcome.code == OutcomeCode.COMPLETED
    assert outcome.money_moved is True
    assert outcome.audit_ref is not None
    assert outcome.audit_ref.startswith("MC-")

    # Verify money movement in target core banking simulator
    assert core_bank_state.members["8830142"].balance == 1580.50
    credit_record = core_bank_state.find_credit_by_case("D-REPLAY-9901")
    assert credit_record is not None
    assert credit_record.amount == 340.00
    assert credit_record.memo_code == outcome.audit_ref

    # CRITICAL ARCHITECTURAL INVARIANT: Replay must execute with ZERO LLM calls!
    assert llm_tracker.call_count == 0, (
        f"Replay of compiled artifact violated invariant: made {llm_tracker.call_count} LLM calls!"
    )
