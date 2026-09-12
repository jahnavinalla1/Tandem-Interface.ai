"""Integration tests for the 14-step effect protocol, prechecks, and control-scoped guards."""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from simulators.core_bank.state import core_bank_state
from tandem.domain.capability import (
    CapabilityDefinition,
    ScopedGuardSpec,
    StepAction,
    StepDefinition,
    load_capability_from_yaml,
)
from tandem.domain.effects import (
    BoundsSpec,
    EffectClass,
    EffectIdentitySpec,
    EffectSpec,
    PostcheckSpec,
    PrecheckSpec,
    ReconciliationSpec,
)
from tandem.domain.errors import EntityBindingMismatchError
from tandem.domain.outcomes import OutcomeCategory, OutcomeCode
from tandem.ledger.database import get_engine, get_session_factory, init_db
from tandem.replay.engine import EffectEngine
from tandem.replay.executor import DeterministicExecutor
from tandem.replay.guards import verify_control_scoped_guard
from tandem.surfaces.playwright_surface import PlaywrightSurface
from tests.server_utils import ensure_simulators_running


@pytest.fixture(scope="module", autouse=True)
def setup_simulators():
    ensure_simulators_running()


@pytest.fixture
def temp_session(tmp_path: Path):
    db_file = tmp_path / "test_effect_protocol.db"
    engine = get_engine(str(db_file))
    init_db(engine)
    session_maker = get_session_factory(engine)
    with session_maker() as session:
        yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def reset_bank_state():
    core_bank_state.seed()


def test_policy_bound_blocks_excessive_credit(temp_session):
    cap = load_capability_from_yaml("capabilities/core/post_provisional_credit.yaml")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        engine = EffectEngine(session=temp_session, page=page)
        outcome = engine.execute_capability(
            capability=cap,
            inputs={"member_id": "8830142", "case_id": "D-9999", "amount": 750.00},  # > $500 limit
        )
        browser.close()

    assert outcome.category == OutcomeCategory.BUSINESS_OUTCOME
    assert outcome.code == OutcomeCode.POLICY_DENIED
    assert "exceeds maximum automated limit" in outcome.message
    assert outcome.money_moved is False


def test_precheck_prevents_duplicate_money_movement(temp_session):
    cap = load_capability_from_yaml("capabilities/core/post_provisional_credit.yaml")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        engine = EffectEngine(session=temp_session, page=page)

        # 1. First execution: Posts credit
        outcome_1 = engine.execute_capability(
            capability=cap,
            inputs={"member_id": "8830142", "case_id": "D-8842", "amount": 340.00},
        )
        assert (
            outcome_1.category == OutcomeCategory.SUCCESS
        ), f"Outcome 1 failed: {outcome_1.code} - {outcome_1.message}"
        assert outcome_1.code == OutcomeCode.COMPLETED
        assert outcome_1.money_moved is True
        first_memo = outcome_1.audit_ref

        # Check bank balance: 1240.50 + 340.00 = 1580.50
        assert core_bank_state.members["8830142"].balance == 1580.50

        # 2. Second execution with SAME case: Precheck intercepts!
        outcome_2 = engine.execute_capability(
            capability=cap,
            inputs={"member_id": "8830142", "case_id": "D-8842", "amount": 340.00},
        )

        browser.close()

    # Verify precheck stopped execution before browser action
    assert outcome_2.category == OutcomeCategory.BUSINESS_OUTCOME
    assert outcome_2.code == OutcomeCode.ALREADY_APPLIED
    assert outcome_2.money_moved is False
    assert outcome_2.audit_ref == first_memo

    # Balance must remain exactly 1580.50 (ZERO DUPLICATE CREDIT!)
    assert core_bank_state.members["8830142"].balance == 1580.50
    assert len(core_bank_state.credits) == 1


def test_control_scoped_guard_detects_transposed_member_in_container():
    """Unit test for verify_control_scoped_guard verifying entity binding."""
    cap = CapabilityDefinition(
        id="core.test_commit",
        name="Test Commit",
        description="Testing guard",
        system="core_bank",
        effect=EffectSpec(
            effect_class=EffectClass.COMMIT,
            idempotency_key="test:{{input.case_id}}",
            identity=EffectIdentitySpec(
                institution_id="alpha",
                procedure_id="reg_e_dispute",
                case_id="{{input.case_id}}",
                capability_id="core.test_commit",
                member_id="{{input.member_id}}",
                account_id="CHK-8830142-01",
                amount="{{input.amount}}",
                currency="USD",
                business_reference="{{input.case_id}}",
            ),
            precheck=PrecheckSpec(capability="core.find"),
            postcheck=PostcheckSpec(capability="core.find"),
            reconciliation=ReconciliationSpec(),
            bounds=BoundsSpec(max_amount=500.0),
        ),
        input_schema={"type": "object"},
        scoped_guard=ScopedGuardSpec(
            guard_id="test_commit_guard",
            container_selector=".confirm-panel, div[id^='commit_scope_container']",
            expected_member_template="{{input.member_id}}",
            expected_amount_template="{{input.amount}}",
        ),
        steps=[
            StepDefinition(
                step_id="step_commit",
                action=StepAction.SUBMIT,
                semantic_target="Test actuation",
                locator_candidates=[".btn-commit-final"],
                guard_ref="test_commit_guard",
            )
        ],
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Navigate to confirmation panel displaying confusable member 8830124
        page.goto("http://127.0.0.1:8001/workspace/credit/entry?member_id=8830124")
        page.fill("input[name='case_id']", "D-TRANSPOSE-TEST")
        page.fill("input[name='amount']", "340.00")
        page.click("button.btn-proceed")

        surface = PlaywrightSurface(page)

        # Expecting 8830142, but page container has 8830124
        with pytest.raises(EntityBindingMismatchError) as exc_info:
            verify_control_scoped_guard(
                capability=cap,
                inputs={"member_id": "8830142", "case_id": "D-TRANSPOSE-TEST", "amount": 340.00},
                surface=surface,
            )

        assert "expected member '8830142'" in str(exc_info.value)
        assert "observed '8830124'" in str(exc_info.value)

        browser.close()


def test_executor_halts_on_transposed_member():
    """Full executor run encountering a transposed member halts with HARD_FAILURE."""
    # Build capability whose commit step runs against the transposed member page
    transposed_cap = CapabilityDefinition(
        id="core.test_transposed_replay",
        name="Test Transposed Replay",
        description="Demonstrates wrong-entity guard failure",
        system="core_bank",
        effect=EffectSpec(
            effect_class=EffectClass.COMMIT,
            idempotency_key="test:{{input.case_id}}",
            identity=EffectIdentitySpec(
                institution_id="alpha",
                procedure_id="reg_e_dispute",
                case_id="{{input.case_id}}",
                capability_id="core.test_transposed_replay",
                member_id="{{input.member_id}}",
                account_id="CHK-8830142-01",
                amount="{{input.amount}}",
                currency="USD",
                business_reference="{{input.case_id}}",
            ),
            precheck=PrecheckSpec(capability="core.find"),
            postcheck=PostcheckSpec(capability="core.find"),
            reconciliation=ReconciliationSpec(),
            bounds=BoundsSpec(max_amount=500.0),
        ),
        input_schema={"type": "object"},
        scoped_guard=ScopedGuardSpec(
            guard_id="test_commit_guard",
            container_selector="#commit_scope_container, .confirm-panel",
            expected_member_template="{{input.member_id}}",
            expected_amount_template="{{input.amount}}",
        ),
        steps=[
            StepDefinition(
                step_id="step_commit",
                action=StepAction.SUBMIT,
                semantic_target="Arbitrary human label",
                locator_candidates=[".btn-commit-final"],
                guard_ref="test_commit_guard",
            )
        ],
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Put page on 8830124's confirmation screen
        page.goto("http://127.0.0.1:8001/workspace/credit/entry?member_id=8830124")
        page.fill("input[name='case_id']", "D-TRANSPOSE-99")
        page.fill("input[name='amount']", "340.00")
        page.click("button.btn-proceed")

        executor = DeterministicExecutor(page)
        outcome = executor.execute(
            capability=transposed_cap,
            inputs={"member_id": "8830142", "case_id": "D-TRANSPOSE-99", "amount": 340.00},
        )
        browser.close()

    assert outcome.category == OutcomeCategory.HARD_FAILURE
    assert outcome.code == OutcomeCode.ENTITY_BINDING_MISMATCH
    assert "Control-scoped guard failed" in outcome.message
    assert outcome.money_moved is False

    # Verify no money moved to either member
    assert core_bank_state.members["8830124"].balance == 410.25
    assert core_bank_state.members["8830142"].balance == 1240.50
