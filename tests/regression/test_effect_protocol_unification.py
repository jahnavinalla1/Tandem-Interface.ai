"""Processor and notice COMMITs must run through the single effect protocol.

Audit finding H-01: processor and document COMMITs bypassed EffectEngine with
direct HTTP calls, leaving them without atomic claims, prechecks, fenced
settlement, reconciliation, or crash recovery. Every test here verifies the
external simulator's durable store independently of Tandem's own ledger.
"""

from __future__ import annotations

import inspect
import multiprocessing
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import sync_playwright

from simulators.core_bank.state import core_bank_state
from simulators.documents.state import document_state
from simulators.processor.state import processor_state
from tandem.config import settings
from tandem.domain.capability import load_capability_from_yaml
from tandem.domain.effects import EffectClaimStatus
from tandem.domain.outcomes import OutcomeCategory, OutcomeCode
from tandem.ledger.database import get_engine, get_session_factory, init_db
from tandem.ledger.repository import LedgerRepository
from tandem.replay.engine import EffectEngine
from tandem.workflow import reg_e

PROCESSOR_ARTIFACT = "capabilities/processor/file_chargeback.yaml"
NOTICE_ARTIFACT = "capabilities/documents/send_notice.yaml"


def _inputs(case_id: str, **extra: object) -> dict[str, object]:
    return {
        "institution_id": "alpha",
        "member_id": "8830142",
        "account_id": "CHK-8830142-01",
        "case_id": case_id,
        "amount": Decimal("340.00"),
        "currency": "USD",
        **extra,
    }


def _processor_inputs(case_id: str) -> dict[str, object]:
    return _inputs(case_id, card_last4="4112", dispute_reason="10.4 - Fraud")


def _notice_inputs(case_id: str) -> dict[str, object]:
    return _inputs(
        case_id,
        notice_type="REG_E_PROVISIONAL_CREDIT_DISCLOSURE",
        deadline_due_at="2026-09-04 17:00:00 UTC",
    )


@pytest.fixture
def ledger(tmp_path: Path):
    engine = get_engine(str(tmp_path / "unified.db"))
    init_db(engine)
    factory = get_session_factory(engine)
    with factory() as session:
        yield session
    engine.dispose()


def _target_chargeback(case_id: str) -> dict[str, object] | None:
    response = httpx.get(f"{settings.processor_url}/api/chargebacks/{case_id}", timeout=3.0)
    return response.json() if response.status_code == 200 else None


def _target_notice(case_id: str) -> dict[str, object] | None:
    response = httpx.get(f"{settings.documents_url}/api/notices/{case_id}", timeout=3.0)
    return response.json() if response.status_code == 200 else None


# ---------------------------------------------------------------------------
# Static contract: the workflow orchestrates capabilities, it does not commit
# ---------------------------------------------------------------------------


def test_workflow_issues_no_direct_commit_http_calls() -> None:
    source = inspect.getsource(reg_e)
    assert "httpx.post" not in source
    assert "/chargeback/file" not in source
    assert "/notices/send" not in source


def test_processor_and_notice_artifacts_declare_full_commit_contract() -> None:
    for path in (PROCESSOR_ARTIFACT, NOTICE_ARTIFACT):
        capability = load_capability_from_yaml(path)
        assert capability.effect.effect_class.value == "COMMIT"
        assert capability.effect.identity is not None
        assert capability.effect.precheck is not None
        assert capability.effect.postcheck is not None
        assert capability.effect.reconciliation is not None
        assert capability.scoped_guard is not None
        actuations = [step for step in capability.steps if step.action.value == "HTTP_POST"]
        assert len(actuations) == 1
        assert actuations[0].guard_ref == capability.scoped_guard.guard_id


# ---------------------------------------------------------------------------
# Processor: claim -> precheck -> intent -> actuation -> postcheck -> settlement
# ---------------------------------------------------------------------------


def test_processor_commit_leaves_complete_protocol_trail(ledger) -> None:
    capability = load_capability_from_yaml(PROCESSOR_ARTIFACT)
    case_id = "D-UNIFIED-CB"
    outcome = EffectEngine(ledger, page=object()).execute_capability(
        capability, _processor_inputs(case_id)
    )

    assert outcome.category == OutcomeCategory.SUCCESS, outcome.model_dump()
    assert outcome.audit_ref and outcome.audit_ref.startswith("VISA-DISP-")
    assert outcome.money_moved is False

    target = _target_chargeback(case_id)
    assert target is not None
    assert target["effect_count"] == 1
    assert target["network_ref"] == outcome.audit_ref
    assert target["member_id"] == "8830142"
    assert target["account_id"] == "CHK-8830142-01"
    assert target["currency"] == "USD"
    assert Decimal(str(target["amount"])) == Decimal("340.00")

    repo = LedgerRepository(ledger)
    claims = repo.get_effect_claims_for_case(case_id)
    assert [claim.capability_id for claim in claims] == ["processor.file_chargeback"]
    assert claims[0].status == EffectClaimStatus.APPLIED.value
    assert claims[0].member_id == "8830142"
    assert claims[0].account_id == "CHK-8830142-01"
    assert claims[0].amount == Decimal("340.00")

    intent = repo.find_intent(claims[0].idempotency_key)
    assert intent is not None and intent.intent_status == "COMMITTED"

    executions = repo.get_executions_for_case(case_id)
    assert [execution.audit_ref for execution in executions] == [outcome.audit_ref]

    event_types = [event.event_type for event in repo.get_events_for_case(case_id)]
    assert event_types.index("EFFECT_CLAIMED") < event_types.index("EFFECT_INTENT_STAGED")


def test_processor_serial_duplicate_is_short_circuited(ledger) -> None:
    capability = load_capability_from_yaml(PROCESSOR_ARTIFACT)
    case_id = "D-UNIFIED-CB-DUP"
    engine = EffectEngine(ledger, page=object())
    first = engine.execute_capability(capability, _processor_inputs(case_id))
    second = engine.execute_capability(capability, _processor_inputs(case_id))

    assert first.category == OutcomeCategory.SUCCESS
    assert second.code in {OutcomeCode.ALREADY_APPLIED, OutcomeCode.ALREADY_CLAIMED}
    assert second.category == OutcomeCategory.BUSINESS_OUTCOME
    assert processor_state.effect_count(case_id) == 1


def test_processor_precheck_outage_never_reaches_target(
    ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability = load_capability_from_yaml(PROCESSOR_ARTIFACT)
    case_id = "D-UNIFIED-CB-PRECHECK-DOWN"
    monkeypatch.setattr(
        "tandem.replay.precheck.httpx.get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(httpx.ConnectTimeout("down")),
    )
    outcome = EffectEngine(ledger, page=object()).execute_capability(
        capability, _processor_inputs(case_id)
    )
    assert outcome.code == OutcomeCode.PRECHECK_UNAVAILABLE
    assert processor_state.find_by_case(case_id) is None


def test_processor_apply_then_504_reconciles_through_engine(ledger) -> None:
    capability = load_capability_from_yaml(PROCESSOR_ARTIFACT)
    case_id = "D-UNIFIED-CB-504"
    processor_state.timeout_after_submit = True
    try:
        outcome = EffectEngine(ledger, page=object()).execute_capability(
            capability, _processor_inputs(case_id)
        )
    finally:
        processor_state.timeout_after_submit = False

    assert outcome.category == OutcomeCategory.SUCCESS, outcome.model_dump()
    assert outcome.code == OutcomeCode.CONFIRMED_APPLIED
    assert outcome.execution_phase.value == "SUBMIT_CONFIRMED"
    target = _target_chargeback(case_id)
    assert target is not None and target["effect_count"] == 1
    assert target["network_ref"] == outcome.audit_ref

    repo = LedgerRepository(ledger)
    assert repo.get_effect_claims_for_case(case_id)[0].status == EffectClaimStatus.APPLIED.value
    event_types = [event.event_type for event in repo.get_events_for_case(case_id)]
    assert "EFFECT_RECONCILED" in event_types


def test_processor_session_expiry_is_recoverable_and_retryable(ledger) -> None:
    capability = load_capability_from_yaml(PROCESSOR_ARTIFACT)
    case_id = "D-UNIFIED-CB-SESSION"
    engine = EffectEngine(ledger, page=object())
    processor_state.session_expired = True
    try:
        expired = engine.execute_capability(capability, _processor_inputs(case_id))
    finally:
        processor_state.session_expired = False

    assert expired.category == OutcomeCategory.RECOVERABLE_FAILURE, expired.model_dump()
    assert expired.code == OutcomeCode.SESSION_EXPIRED
    assert processor_state.find_by_case(case_id) is None
    repo = LedgerRepository(ledger)
    assert (
        repo.get_effect_claims_for_case(case_id)[0].status
        == EffectClaimStatus.FAILED_RETRYABLE.value
    )

    retried = engine.execute_capability(capability, _processor_inputs(case_id))
    assert retried.category == OutcomeCategory.SUCCESS, retried.model_dump()
    assert processor_state.effect_count(case_id) == 1
    claims = repo.get_effect_claims_for_case(case_id)
    assert len(claims) == 1
    assert claims[0].status == EffectClaimStatus.APPLIED.value
    assert claims[0].fencing_token == 2


def test_processor_uncertain_when_target_applied_but_inquiry_unavailable(ledger) -> None:
    capability = load_capability_from_yaml(PROCESSOR_ARTIFACT)
    case_id = "D-UNIFIED-CB-UNCERTAIN"
    # The processor applies the filing, drops the response, and its inquiry
    # endpoint fails for records that exist: precheck saw absence, postcheck
    # cannot prove anything.
    processor_state.timeout_after_submit = True
    processor_state.fail_lookup_when_present = True
    engine = EffectEngine(ledger, page=object())
    try:
        outcome = engine.execute_capability(capability, _processor_inputs(case_id))
    finally:
        processor_state.timeout_after_submit = False
        processor_state.fail_lookup_when_present = False

    assert outcome.category == OutcomeCategory.UNCERTAIN_EFFECT, outcome.model_dump()
    assert processor_state.effect_count(case_id) == 1
    repo = LedgerRepository(ledger)
    assert repo.get_effect_claims_for_case(case_id)[0].status == EffectClaimStatus.UNCERTAIN.value

    # Uncertainty fences any blind repeat even after the inquiry path recovers.
    fenced = engine.execute_capability(capability, _processor_inputs(case_id))
    assert fenced.code == OutcomeCode.ALREADY_CLAIMED
    assert processor_state.effect_count(case_id) == 1


def test_pre_submit_human_intervention_keeps_claim_retryable(ledger) -> None:
    """A compliance interstitial halts automation before submit; once a human
    clears it, the same effect must be claimable again (audit H-01/H-05 resume)."""
    capability = load_capability_from_yaml("capabilities/core/post_provisional_credit.yaml")
    case_id = "D-UNIFIED-INTERSTITIAL"
    inputs = _inputs(case_id)
    core_bank_state.require_compliance_interstitial = True
    core_bank_state.compliance_cleared = False
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        engine = EffectEngine(ledger, page=page)
        halted = engine.execute_capability(capability, inputs)
        assert halted.category == OutcomeCategory.NEEDS_HUMAN, halted.model_dump()
        assert halted.code == OutcomeCode.COMPLIANCE_INTERSTITIAL
        assert core_bank_state.find_credit_by_case(case_id) is None
        repo = LedgerRepository(ledger)
        claim = repo.get_effect_claims_for_case(case_id)[0]
        assert claim.status == EffectClaimStatus.FAILED_RETRYABLE.value

        # A human clears the interstitial on the same live page, exactly as the
        # operator sign-off path does; automation then re-admits the effect.
        frame = page.frame_locator("#core_workspace_frame")
        frame.locator("button.operator-signoff-btn").click()
        frame.locator("#credit_action_container").wait_for(state="visible", timeout=5000)
        assert core_bank_state.compliance_cleared is True
        resumed = engine.execute_capability(capability, inputs)
        browser.close()

    assert resumed.category == OutcomeCategory.SUCCESS, resumed.model_dump()
    assert core_bank_state.effect_count(case_id) == 1
    claims = repo.get_effect_claims_for_case(case_id)
    assert len(claims) == 1
    assert claims[0].status == EffectClaimStatus.APPLIED.value
    assert claims[0].fencing_token == 2


# ---------------------------------------------------------------------------
# Notice: same protocol, same failure semantics
# ---------------------------------------------------------------------------


def test_notice_commit_leaves_complete_protocol_trail(ledger) -> None:
    capability = load_capability_from_yaml(NOTICE_ARTIFACT)
    case_id = "D-UNIFIED-NOTICE"
    outcome = EffectEngine(ledger, page=object()).execute_capability(
        capability, _notice_inputs(case_id)
    )
    assert outcome.category == OutcomeCategory.SUCCESS, outcome.model_dump()
    assert outcome.audit_ref and outcome.audit_ref.startswith("NOT-")

    target = _target_notice(case_id)
    assert target is not None
    assert target["effect_count"] == 1
    assert target["notice_id"] == outcome.audit_ref
    assert target["member_id"] == "8830142"
    assert target["account_id"] == "CHK-8830142-01"
    assert target["status"] == "SENT"

    repo = LedgerRepository(ledger)
    claims = repo.get_effect_claims_for_case(case_id)
    assert [claim.capability_id for claim in claims] == ["docs.send_notice"]
    assert claims[0].status == EffectClaimStatus.APPLIED.value


def test_notice_serial_duplicate_is_short_circuited(ledger) -> None:
    capability = load_capability_from_yaml(NOTICE_ARTIFACT)
    case_id = "D-UNIFIED-NOTICE-DUP"
    engine = EffectEngine(ledger, page=object())
    engine.execute_capability(capability, _notice_inputs(case_id))
    second = engine.execute_capability(capability, _notice_inputs(case_id))
    assert second.code in {OutcomeCode.ALREADY_APPLIED, OutcomeCode.ALREADY_CLAIMED}
    assert document_state.effect_count(case_id) == 1


def test_notice_failure_without_effect_is_confirmed_absent_and_retryable(ledger) -> None:
    capability = load_capability_from_yaml(NOTICE_ARTIFACT)
    case_id = "D-UNIFIED-NOTICE-500"
    engine = EffectEngine(ledger, page=object())
    document_state.simulate_failure = True
    try:
        failed = engine.execute_capability(capability, _notice_inputs(case_id))
    finally:
        document_state.simulate_failure = False

    assert failed.code == OutcomeCode.CONFIRMED_NOT_APPLIED, failed.model_dump()
    assert failed.is_success is False
    assert document_state.find_by_case(case_id) is None
    repo = LedgerRepository(ledger)
    assert (
        repo.get_effect_claims_for_case(case_id)[0].status
        == EffectClaimStatus.FAILED_RETRYABLE.value
    )

    retried = engine.execute_capability(capability, _notice_inputs(case_id))
    assert retried.category == OutcomeCategory.SUCCESS, retried.model_dump()
    assert document_state.effect_count(case_id) == 1


# ---------------------------------------------------------------------------
# Two independent processes, one processor effect
# ---------------------------------------------------------------------------


def _processor_race_worker(ledger_path: str, case_id: str, barrier, queue) -> None:
    engine = get_engine(ledger_path)
    factory = get_session_factory(engine)
    try:
        capability = load_capability_from_yaml(PROCESSOR_ARTIFACT)
        barrier.wait(timeout=10)
        with factory() as session:
            outcome = EffectEngine(session, page=object()).execute_capability(
                capability, _processor_inputs(case_id)
            )
            queue.put(outcome.code.value)
    finally:
        engine.dispose()


def test_two_processes_produce_one_processor_effect_repeated(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "processor-race.db")
    engine = get_engine(ledger_path)
    init_db(engine)
    engine.dispose()

    context = multiprocessing.get_context("spawn")
    for attempt in range(5):
        case_id = f"D-CB-RACE-{attempt:02d}"
        barrier = context.Barrier(2)
        queue = context.Queue()
        workers = [
            context.Process(
                target=_processor_race_worker, args=(ledger_path, case_id, barrier, queue)
            )
            for _ in range(2)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=60)
            assert worker.exitcode == 0

        results = sorted(queue.get(timeout=5) for _ in workers)
        assert results.count("COMPLETED") == 1, results
        assert sum(code in {"ALREADY_CLAIMED", "ALREADY_APPLIED"} for code in results) == 1

        target = _target_chargeback(case_id)
        assert target is not None
        assert target["effect_count"] == 1
