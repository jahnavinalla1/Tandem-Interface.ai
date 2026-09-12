"""Independent adversarial checks for Tandem's stated safety invariants.

These tests express the behavior promised by the specification. A failure is
evidence that the current implementation does not enforce the corresponding
invariant; these are not regression tests for the repository's current behavior.
"""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from playwright.sync_api import sync_playwright
from pydantic import ValidationError

from simulators.core_bank.state import core_bank_state
from tandem.domain.capability import (
    CapabilityDefinition,
    ScopedGuardSpec,
    load_capability_from_yaml,
)
from tandem.domain.effects import BoundsSpec, EffectClass, EffectSpec, PostcheckSpec, PrecheckSpec
from tandem.domain.errors import (
    EntityBindingMismatchError,
    LeaseConflictError,
)
from tandem.domain.outcomes import ExecutionOutcome, OutcomeCategory, OutcomeCode
from tandem.ledger.database import get_engine, get_session_factory, init_db
from tandem.ledger.repository import LedgerRepository
from tandem.policy.engine import PolicyEngine
from tandem.replay.engine import EffectEngine
from tandem.replay.executor import DeterministicExecutor
from tandem.replay.guards import verify_control_scoped_guard
from tandem.replay.precheck import execute_precheck
from tandem.surfaces.base import ObservedRecord
from tandem.workflow.reg_e import RegEWorkflow
from tests.server_utils import ensure_simulators_running, reset_all_simulators


@pytest.fixture(scope="session", autouse=True)
def simulator_servers():
    ensure_simulators_running()


@pytest.fixture(autouse=True)
def clean_external_state():
    reset_all_simulators()
    yield
    reset_all_simulators()


def _credit_capability():
    return load_capability_from_yaml("capabilities/core/post_provisional_credit.yaml")


def test_tampered_compiled_artifact_is_rejected(tmp_path):
    source = "capabilities/compiled/demo_post_provisional_credit.yaml"
    tampered = tmp_path / "tampered.yaml"
    text = open(source, encoding="utf-8").read()
    text = text.replace("button.btn-commit-final", "button.attacker-controlled", 1)
    tampered.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="hash|integrity|tamper"):
        load_capability_from_yaml(str(tampered))


def test_empty_commit_capability_is_rejected_by_schema():
    with pytest.raises(ValidationError):
        CapabilityDefinition(
            id="core.empty_commit",
            name="Empty commit",
            description="A COMMIT artifact with no executable or guard step",
            system="core_bank",
            effect=EffectSpec(
                effect_class=EffectClass.COMMIT,
                idempotency_key="test:{{input.case_id}}",
                precheck=PrecheckSpec(capability="core.find"),
                postcheck=PostcheckSpec(capability="core.find"),
                bounds=BoundsSpec(max_amount=500.0),
            ),
            input_schema={"type": "object"},
            scoped_guard=ScopedGuardSpec(container_selector="#scope"),
        )


def test_guard_fails_closed_when_identity_evidence_is_missing():
    class MissingEvidenceSurface:
        def observe_container(self, **_kwargs):
            return ObservedRecord(container_selector="#scope")

    cap = _credit_capability()
    with pytest.raises(EntityBindingMismatchError):
        verify_control_scoped_guard(
            capability=cap,
            inputs={"member_id": "8830142", "case_id": "D-MISSING", "amount": 340.0},
            surface=MissingEvidenceSurface(),
        )


def test_precheck_outage_is_not_treated_as_effect_absent(monkeypatch):
    cap = _credit_capability()

    def unavailable(*_args, **_kwargs):
        raise OSError("precheck transport unavailable")

    monkeypatch.setattr("tandem.replay.precheck.httpx.get", unavailable)
    outcome = execute_precheck(
        cap, {"member_id": "8830142", "case_id": "D-PRECHECK-DOWN", "amount": 340.0}
    )

    assert outcome is not None
    assert outcome.code == OutcomeCode.PRECHECK_UNAVAILABLE


def test_active_human_lease_cannot_be_overwritten_by_automation(tmp_path):
    engine = get_engine(str(tmp_path / "lease.db"))
    init_db(engine)
    session_factory = get_session_factory(engine)
    with session_factory() as session:
        repo = LedgerRepository(session)
        repo.create_or_get_case("D-LEASE", "8830142", 340.0)
        repo.acquire_lease("D-LEASE", "operator_sarah")
        session.commit()

        with pytest.raises(LeaseConflictError):
            repo.acquire_lease("D-LEASE", "AUTOMATION")

    engine.dispose()


def test_nan_amount_is_denied_by_policy():
    outcome = PolicyEngine.evaluate(
        _credit_capability(),
        {"member_id": "8830142", "case_id": "D-NAN", "amount": float("nan")},
    )
    assert outcome is not None
    assert outcome.code == OutcomeCode.POLICY_DENIED


def test_wrong_case_in_commit_container_is_blocked():
    cap = _credit_capability().model_copy(deep=True)
    cap.steps = [cap.steps[-1]]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://127.0.0.1:8001/workspace/credit/entry?member_id=8830142")
        page.locator("input[name='case_id']").fill("D-ACTUAL")
        page.locator("input[name='amount']").fill("340.00")
        page.locator("button.btn-proceed").click()

        outcome = DeterministicExecutor(page).execute(
            cap,
            {"member_id": "8830142", "case_id": "D-EXPECTED", "amount": 340.0},
        )
        browser.close()

    assert outcome.category == OutcomeCategory.HARD_FAILURE
    assert outcome.code == OutcomeCode.ENTITY_BINDING_MISMATCH
    assert core_bank_state.find_credit_by_case("D-ACTUAL") is None


def test_submitted_amount_cannot_diverge_after_visible_guard():
    cap = _credit_capability().model_copy(deep=True)
    cap.steps = [cap.steps[-1]]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://127.0.0.1:8001/workspace/credit/entry?member_id=8830142")
        page.locator("input[name='case_id']").fill("D-AMOUNT-BIND")
        page.locator("input[name='amount']").fill("340.00")
        page.locator("button.btn-proceed").click()
        page.locator("form[action$='/commit'] input[name='amount']").evaluate(
            "element => element.value = '34.00'"
        )

        outcome = DeterministicExecutor(page).execute(
            cap,
            {"member_id": "8830142", "case_id": "D-AMOUNT-BIND", "amount": 340.0},
        )
        browser.close()

    assert outcome.category == OutcomeCategory.HARD_FAILURE
    assert outcome.code == OutcomeCode.AMOUNT_MISMATCH
    assert core_bank_state.members["8830142"].balance == 1240.50


def test_resume_reconciles_external_state_after_simulator_restart(tmp_path):
    db_path = str(tmp_path / "restart.db")
    engine_1 = get_engine(db_path)
    init_db(engine_1)
    factory_1 = get_session_factory(engine_1)

    with factory_1() as session_1, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        workflow = RegEWorkflow(session_1, page, kill_after_credit=True)
        with pytest.raises(RuntimeError, match="PROCESS_KILL_AFTER"):
            workflow.run_case("D-RESTART", "8830142", 340.0)
        browser.close()
    engine_1.dispose()

    assert core_bank_state.find_credit_by_case("D-RESTART") is not None
    core_bank_state.seed()  # Equivalent to restarting the in-memory target simulator.

    engine_2 = get_engine(db_path)
    factory_2 = get_session_factory(engine_2)
    with factory_2() as session_2, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        result = RegEWorkflow(session_2, page).run_case("D-RESTART", "8830142", 340.0)
        browser.close()

    # The original audit expected replay to recreate the missing target effect.
    # That would blindly repeat a ledger-APPLIED COMMIT. The stronger Phase 12
    # contract requires explicit divergence and human review instead.
    assert result["status"] == "NEEDS_HUMAN"
    assert result["code"] == OutcomeCode.TARGET_LEDGER_DIVERGENCE.value
    assert core_bank_state.find_credit_by_case("D-RESTART") is None
    assert core_bank_state.members["8830142"].balance == 1240.50
    engine_2.dispose()


def test_two_workers_cannot_both_commit_same_case(tmp_path):
    db_path = str(tmp_path / "race.db")
    engine = get_engine(db_path)
    init_db(engine)
    factory = get_session_factory(engine)
    with factory() as session:
        repo = LedgerRepository(session)
        repo.create_or_get_case("D-RACE", "8830142", 340.0)
        repo.acquire_lease("D-RACE", "AUTOMATION")
        repo.release_lease("D-RACE")
        session.commit()
    engine.dispose()

    barrier = threading.Barrier(2)

    def worker():
        worker_engine = get_engine(db_path)
        worker_factory = get_session_factory(worker_engine)
        try:
            # Synchronize before admission. A barrier inside the mutation becomes
            # unreachable by design once the durable claim correctly admits one worker.
            barrier.wait(timeout=10)
            with worker_factory() as session:
                effect_engine = EffectEngine(session=session, page=object())

                def simulated_browser_commit(*_args, **_kwargs):
                    credit = core_bank_state.post_credit("D-RACE", "8830142", 340.0)
                    return ExecutionOutcome(
                        category=OutcomeCategory.SUCCESS,
                        code=OutcomeCode.COMPLETED,
                        message="simulated commit",
                        money_moved=True,
                        audit_ref=credit.memo_code,
                    )

                effect_engine.executor.execute = simulated_browser_commit
                return effect_engine.execute_capability(
                    _credit_capability(),
                    {"member_id": "8830142", "case_id": "D-RACE", "amount": 340.0},
                )
        finally:
            worker_engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        outcomes = [future.result(timeout=45) for future in futures]

    assert all(
        outcome.code
        in {OutcomeCode.COMPLETED, OutcomeCode.ALREADY_APPLIED, OutcomeCode.ALREADY_CLAIMED}
        for outcome in outcomes
    )
    assert sum(outcome.code == OutcomeCode.COMPLETED for outcome in outcomes) == 1
    assert core_bank_state.members["8830142"].balance == 1580.50
