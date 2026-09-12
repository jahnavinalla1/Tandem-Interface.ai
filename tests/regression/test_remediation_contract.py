"""Behavioral contracts for the audited safety and artifact boundaries."""

from __future__ import annotations

import inspect
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import yaml
from playwright.sync_api import sync_playwright
from pydantic import ValidationError

from simulators.core_bank.state import core_bank_state
from tandem.discovery.agent import DiscoveryAgent
from tandem.discovery.recorder import DiscoveryTrace
from tandem.domain.capability import (
    CapabilityDefinition,
    compute_artifact_digest,
    load_capability_from_yaml,
)
from tandem.domain.effects import EffectSpec
from tandem.domain.outcomes import OutcomeCategory, OutcomeCode
from tandem.ledger.database import get_engine, get_session_factory, init_db
from tandem.policy.engine import PolicyEngine
from tandem.replay.engine import EffectEngine
from tandem.replay.executor import DeterministicExecutor
from tandem.replay.postcheck import execute_postcheck
from tandem.replay.precheck import execute_precheck
from tandem.replay.reconciliation import reconcile_commit_execution

ARTIFACT = Path("capabilities/compiled/demo_post_provisional_credit.yaml")


def _artifact_data() -> dict[str, object]:
    return yaml.safe_load(ARTIFACT.read_text(encoding="utf-8"))


def _write_tampered(tmp_path: Path, mutation) -> Path:
    data = _artifact_data()
    mutation(data)
    target = tmp_path / "tampered.yaml"
    target.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return target


def test_discovery_contract_requires_real_provider_and_durable_evidence() -> None:
    constructor = inspect.signature(DiscoveryAgent)
    assert "provider" in constructor.parameters
    assert "run_id" in DiscoveryTrace.model_fields
    assert "provider" in DiscoveryTrace.model_fields
    assert "model" in DiscoveryTrace.model_fields
    assert "events" in DiscoveryTrace.model_fields
    assert "evidence_directory" in DiscoveryTrace.model_fields


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data["steps"][0].update(semantic_target="http://evil.invalid"),
        lambda data: data["effect"].update({"class": "READ"}),
        lambda data: data["effect"]["bounds"].update(max_amount="999999.99"),
    ],
    ids=["url", "effect-class", "policy-bound"],
)
def test_artifact_tampering_variants_are_rejected(tmp_path: Path, mutation) -> None:
    tampered = _write_tampered(tmp_path, mutation)
    with pytest.raises(ValueError, match="hash|integrity|tamper|digest"):
        load_capability_from_yaml(str(tampered))


def test_unsupported_artifact_schema_version_is_rejected(tmp_path: Path) -> None:
    assert "schema_version" in CapabilityDefinition.model_fields
    tampered = _write_tampered(tmp_path, lambda data: data.update(schema_version=999))
    with pytest.raises(ValueError, match="schema|version|unsupported"):
        load_capability_from_yaml(str(tampered))


def test_rehashed_external_navigation_target_is_still_rejected(tmp_path: Path) -> None:
    data = _artifact_data()
    data["steps"][0]["semantic_target"] = "https://evil.invalid/steal"
    normalized = CapabilityDefinition.model_validate(data).model_dump(
        by_alias=True, mode="json", exclude={"artifact_hash"}
    )
    data["artifact_hash"] = compute_artifact_digest(normalized)
    target = tmp_path / "rehashed-evil.yaml"
    target.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="allowlisted logical surface|Navigation target"):
        load_capability_from_yaml(str(target))


def test_commit_contract_requires_structural_guard_and_reconciliation() -> None:
    assert "identity" in EffectSpec.model_fields
    assert "guard_ref" in CapabilityDefinition.model_fields["steps"].annotation.__args__[0].model_fields

    data = _artifact_data()
    data["effect"]["reconciliation"] = None
    data["steps"][-1]["guard_ref"] = None
    with pytest.raises(ValidationError):
        CapabilityDefinition.model_validate(data)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.update(steps=[]),
        lambda data: data["steps"][-1].update(action="CLICK"),
        lambda data: data.update(scoped_guard=None),
        lambda data: data["steps"][-1].update(guard_ref=None),
        lambda data: data["steps"][-1].update(guard_ref="unrelated_guard"),
        lambda data: data["effect"].update(identity=None),
        lambda data: data["effect"].update(precheck=None),
        lambda data: data["effect"].update(postcheck=None),
        lambda data: data["effect"].update(reconciliation=None),
    ],
    ids=[
        "zero-steps",
        "no-mutating-actuation",
        "no-guard",
        "no-actuation-guard-ref",
        "wrong-actuation-guard-ref",
        "no-effect-identity",
        "no-precheck",
        "no-postcheck",
        "no-reconciliation",
    ],
)
def test_invalid_commit_topologies_are_rejected(mutation) -> None:
    data = _artifact_data()
    mutation(data)
    with pytest.raises(ValidationError):
        CapabilityDefinition.model_validate(data)


def test_commit_safety_is_independent_of_human_readable_label() -> None:
    data = _artifact_data()
    data["steps"][-1]["semantic_target"] = "Anything an artifact author wants to call it"
    capability = CapabilityDefinition.model_validate(data)
    assert capability.steps[-1].action.value == "SUBMIT"
    assert capability.steps[-1].guard_ref == capability.scoped_guard.guard_id
    assert 'semantic_target == "Commit Button"' not in inspect.getsource(
        DeterministicExecutor.execute
    )


def test_postcheck_outage_is_explicit_and_never_success(monkeypatch: pytest.MonkeyPatch) -> None:
    capability = load_capability_from_yaml(str(ARTIFACT))

    def unavailable(*_args, **_kwargs):
        raise httpx.ConnectTimeout("target unavailable")

    monkeypatch.setattr("tandem.replay.postcheck.httpx.get", unavailable)
    outcome = execute_postcheck(
        capability,
        {
            "institution_id": "alpha",
            "member_id": "8830142",
            "account_id": "CHK-8830142-01",
            "case_id": "D-POSTCHECK-DOWN",
            "amount": "340.00",
            "currency": "USD",
        },
    )
    assert outcome is not None
    assert outcome.category != OutcomeCategory.SUCCESS
    assert outcome.code in {
        OutcomeCode.POSTCHECK_UNAVAILABLE,
        OutcomeCode.POSTCHECK_UNCERTAIN,
        OutcomeCode.UNCERTAIN_EFFECT,
    }


def test_postcheck_outage_after_submit_never_reports_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = load_capability_from_yaml(str(ARTIFACT))
    inputs = {
        "institution_id": "alpha",
        "member_id": "8830142",
        "account_id": "CHK-8830142-01",
        "case_id": "D-POSTCHECK-AFTER-SUBMIT",
        "amount": "340.00",
        "currency": "USD",
    }

    monkeypatch.setattr(
        "tandem.replay.postcheck.httpx.get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(httpx.ConnectTimeout("down")),
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        outcome = DeterministicExecutor(browser.new_page()).execute(capability, inputs)
        browser.close()

    assert core_bank_state.find_credit_by_case(inputs["case_id"]) is not None
    assert outcome.category == OutcomeCategory.UNCERTAIN_EFFECT
    assert outcome.code == OutcomeCode.POSTCHECK_UNAVAILABLE
    assert outcome.is_success is False


@pytest.mark.parametrize(
    ("status_code", "payload", "expected_code"),
    [
        (404, {"error": "absent"}, OutcomeCode.CONFIRMED_NOT_APPLIED),
        (500, {"error": "down"}, OutcomeCode.POSTCHECK_UNAVAILABLE),
        (200, None, OutcomeCode.POSTCHECK_INVALID_RESPONSE),
        (200, {"case_id": "D-POSTCHECK", "status": "POSTED"}, OutcomeCode.POSTCHECK_AMBIGUOUS),
        (
            200,
            {
                "case_id": "D-OTHER",
                "member_id": "8830142",
                "account_id": "CHK-8830142-01",
                "amount": "340.00",
                "memo_code": "MC-1",
                "status": "POSTED",
            },
            OutcomeCode.POSTCHECK_AMBIGUOUS,
        ),
    ],
    ids=["confirmed-absent", "http-500", "malformed-json", "missing-identity", "mismatch"],
)
def test_postcheck_states_are_explicit(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    payload: dict[str, object] | None,
    expected_code: OutcomeCode,
) -> None:
    capability = load_capability_from_yaml(str(ARTIFACT))

    class Response:
        def __init__(self) -> None:
            self.status_code = status_code

        def json(self):
            if payload is None:
                raise ValueError("not json")
            return payload

    monkeypatch.setattr("tandem.replay.postcheck.httpx.get", lambda *_args, **_kwargs: Response())
    outcome = execute_postcheck(
        capability,
        {
            "institution_id": "alpha",
            "member_id": "8830142",
            "account_id": "CHK-8830142-01",
            "case_id": "D-POSTCHECK",
            "amount": "340.00",
            "currency": "USD",
        },
    )
    assert outcome.code == expected_code
    if expected_code == OutcomeCode.CONFIRMED_NOT_APPLIED:
        assert outcome.category == OutcomeCategory.BUSINESS_OUTCOME
    else:
        assert outcome.category == OutcomeCategory.UNCERTAIN_EFFECT


def test_reconciliation_retries_transient_inquiry_outage_before_declaring_uncertain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M-03: `reconciliation.max_inquiry_attempts` was declared but never read.

    A transient postcheck-*inquiry* outage (not the mutating COMMIT itself) must now be
    retried, bounded by `max_inquiry_attempts`, before an effect is escalated as
    UNCERTAIN_EFFECT -- without ever retrying the mutation.
    """
    capability = load_capability_from_yaml(str(ARTIFACT))
    assert capability.effect.reconciliation is not None
    assert capability.effect.reconciliation.max_inquiry_attempts >= 2

    inputs = {
        "institution_id": "alpha",
        "member_id": "8830142",
        "account_id": "CHK-8830142-01",
        "case_id": "D-RECONCILE-RETRY",
        "amount": "340.00",
        "currency": "USD",
    }

    # Actually post the credit first so a later, successful postcheck inquiry has a
    # real effect to find.
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        outcome = DeterministicExecutor(browser.new_page()).execute(capability, inputs)
        browser.close()
    assert outcome.code == OutcomeCode.COMPLETED

    real_get = httpx.get
    calls = {"count": 0}

    def flaky_then_real(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise httpx.ConnectTimeout("transient inquiry outage")
        return real_get(*args, **kwargs)

    monkeypatch.setattr("tandem.replay.postcheck.httpx.get", flaky_then_real)

    reconciled = reconcile_commit_execution(
        capability=capability, inputs=inputs, error_message="connection reset mid-submit"
    )
    assert calls["count"] == 2, "expected exactly one retry of the read-only inquiry"
    assert reconciled.category == OutcomeCategory.SUCCESS
    assert reconciled.code == OutcomeCode.CONFIRMED_APPLIED
    assert reconciled.details["inquiry_attempts"] == 2


def test_reconciliation_never_retries_an_ambiguous_inquiry_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed/ambiguous inquiry response is not retried: re-querying immediately
    would not fix bad data, so it is classified as uncertain on the first attempt."""
    capability = load_capability_from_yaml(str(ARTIFACT))

    class Response:
        status_code = 200

        def json(self):
            return {"case_id": "D-RECONCILE-AMBIGUOUS", "status": "POSTED"}

    calls = {"count": 0}

    def always_ambiguous(*_args, **_kwargs):
        calls["count"] += 1
        return Response()

    monkeypatch.setattr("tandem.replay.postcheck.httpx.get", always_ambiguous)

    reconciled = reconcile_commit_execution(
        capability=capability,
        inputs={
            "institution_id": "alpha",
            "member_id": "8830142",
            "account_id": "CHK-8830142-01",
            "case_id": "D-RECONCILE-AMBIGUOUS",
            "amount": "340.00",
            "currency": "USD",
        },
        error_message="connection reset mid-submit",
    )
    assert calls["count"] == 1
    assert reconciled.category == OutcomeCategory.UNCERTAIN_EFFECT
    assert reconciled.details["inquiry_attempts"] == 1


@pytest.mark.parametrize(
    ("status_code", "payload", "expected_code"),
    [
        (500, {"error": "down"}, OutcomeCode.PRECHECK_UNAVAILABLE),
        (200, None, OutcomeCode.PRECHECK_INVALID_RESPONSE),
        (200, {"case_id": "D-PRECHECK", "status": "POSTED"}, OutcomeCode.PRECHECK_AMBIGUOUS),
        (
            200,
            {
                "case_id": "D-OTHER",
                "member_id": "8830142",
                "account_id": "CHK-8830142-01",
                "amount": "340.00",
                "memo_code": "MC-1",
                "status": "POSTED",
            },
            OutcomeCode.PRECHECK_AMBIGUOUS,
        ),
    ],
    ids=["http-500", "malformed-json", "missing-identity", "identity-mismatch"],
)
def test_precheck_non_absence_states_halt(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    payload: dict[str, object] | None,
    expected_code: OutcomeCode,
) -> None:
    capability = load_capability_from_yaml(str(ARTIFACT))

    class Response:
        def __init__(self) -> None:
            self.status_code = status_code

        def json(self):
            if payload is None:
                raise ValueError("not json")
            return payload

    monkeypatch.setattr("tandem.replay.precheck.httpx.get", lambda *_args, **_kwargs: Response())
    outcome = execute_precheck(
        capability,
        {
            "institution_id": "alpha",
            "member_id": "8830142",
            "account_id": "CHK-8830142-01",
            "case_id": "D-PRECHECK",
            "amount": "340.00",
            "currency": "USD",
        },
    )
    assert outcome.code == expected_code
    assert outcome.category == OutcomeCategory.HARD_FAILURE


def test_precheck_outage_never_reaches_external_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    capability = load_capability_from_yaml(str(ARTIFACT))
    monkeypatch.setattr(
        "tandem.replay.precheck.httpx.get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(httpx.ConnectTimeout("down")),
    )
    engine = get_engine(str(tmp_path / "precheck-halt.db"))
    init_db(engine)
    factory = get_session_factory(engine)
    with factory() as session:
        effect_engine = EffectEngine(session, page=object())
        effect_engine.executor.execute = lambda *_args, **_kwargs: pytest.fail(
            "external mutation was reached while precheck was unavailable"
        )
        outcome = effect_engine.execute_capability(
            capability,
            {
                "institution_id": "alpha",
                "member_id": "8830142",
                "account_id": "CHK-8830142-01",
                "case_id": "D-PRECHECK-HALT",
                "amount": "340.00",
                "currency": "USD",
            },
        )
    assert outcome.code == OutcomeCode.PRECHECK_UNAVAILABLE


def test_guard_contract_requires_account_binding() -> None:
    guard_type = CapabilityDefinition.model_fields["scoped_guard"].annotation
    guard_model = next(arg for arg in guard_type.__args__ if arg is not type(None))
    assert "expected_account_template" in guard_model.model_fields


def test_guard_contract_requires_currency_binding() -> None:
    guard_type = CapabilityDefinition.model_fields["scoped_guard"].annotation
    guard_model = next(arg for arg in guard_type.__args__ if arg is not type(None))
    assert "expected_currency_template" in guard_model.model_fields


@pytest.mark.parametrize(
    ("field", "submitted_value", "expected_code"),
    [
        ("institution_id", "other-bank", OutcomeCode.ENTITY_BINDING_MISMATCH),
        ("member_id", "8830124", OutcomeCode.ENTITY_BINDING_MISMATCH),
        ("account_id", "CHK-WRONG-01", OutcomeCode.ENTITY_BINDING_MISMATCH),
        ("case_id", "D-WRONG-CASE", OutcomeCode.ENTITY_BINDING_MISMATCH),
        ("amount", "34.00", OutcomeCode.AMOUNT_MISMATCH),
        ("currency", "EUR", OutcomeCode.ENTITY_BINDING_MISMATCH),
    ],
)
def test_guard_binds_exact_owning_form_submission(
    field: str,
    submitted_value: str,
    expected_code: OutcomeCode,
) -> None:
    capability = load_capability_from_yaml("capabilities/core/post_provisional_credit.yaml")
    capability.steps = [capability.steps[-1]]
    inputs = {
        "institution_id": "alpha",
        "member_id": "8830142",
        "account_id": "CHK-8830142-01",
        "case_id": "D-FORM-BINDING",
        "amount": "340.00",
        "currency": "USD",
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://127.0.0.1:8001/workspace/credit/entry?member_id=8830142")
        page.locator("input[name='case_id']").fill(inputs["case_id"])
        page.locator("input[name='amount']").fill(inputs["amount"])
        page.locator("button.btn-proceed").click()
        page.locator(f"form[action$='/commit'] [name='{field}']").evaluate(
            "(element, value) => element.value = value", submitted_value
        )
        outcome = DeterministicExecutor(page).execute(capability, inputs)
        browser.close()

    assert outcome.category == OutcomeCategory.HARD_FAILURE
    assert outcome.code == expected_code
    assert core_bank_state.credits == {}


def test_guard_rejects_missing_owning_form_evidence() -> None:
    capability = load_capability_from_yaml("capabilities/core/post_provisional_credit.yaml")
    capability.steps = [capability.steps[-1]]
    inputs = {
        "institution_id": "alpha",
        "member_id": "8830142",
        "account_id": "CHK-8830142-01",
        "case_id": "D-FORM-MISSING",
        "amount": "340.00",
        "currency": "USD",
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://127.0.0.1:8001/workspace/credit/entry?member_id=8830142")
        page.locator("input[name='case_id']").fill(inputs["case_id"])
        page.locator("input[name='amount']").fill(inputs["amount"])
        page.locator("button.btn-proceed").click()
        page.locator("form[action$='/commit'] input[name='account_id']").evaluate(
            "element => element.remove()"
        )
        outcome = DeterministicExecutor(page).execute(capability, inputs)
        browser.close()

    assert outcome.category == OutcomeCategory.HARD_FAILURE
    assert outcome.code == OutcomeCode.ENTITY_BINDING_MISMATCH
    assert core_bank_state.credits == {}


@pytest.mark.parametrize("bad_value", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_money_is_rejected_at_input_boundary(bad_value: str) -> None:
    capability = load_capability_from_yaml(str(ARTIFACT))
    outcome = PolicyEngine.evaluate(
        capability,
        {
            "institution_id": "alpha",
            "member_id": "8830142",
            "account_id": "CHK-8830142-01",
            "case_id": "D-NON-FINITE",
            "amount": bad_value,
            "currency": "USD",
        },
    )
    assert outcome is not None
    assert outcome.code in {OutcomeCode.POLICY_DENIED, OutcomeCode.POLICY_VIOLATION}


@pytest.mark.parametrize(
    ("amount", "allowed"),
    [
        ("0.00", False),
        ("0.01", True),
        ("340.00", True),
        ("499.99", True),
        ("500.00", True),
        ("500.01", False),
        ("999999999999999999.99", False),
    ],
)
def test_money_policy_boundary_table(amount: str, allowed: bool) -> None:
    capability = load_capability_from_yaml(str(ARTIFACT))
    outcome = PolicyEngine.evaluate(
        capability,
        {
            "institution_id": "alpha",
            "member_id": "8830142",
            "account_id": "CHK-8830142-01",
            "case_id": f"D-BOUND-{amount}",
            "amount": Decimal(amount),
            "currency": "USD",
        },
    )
    assert (outcome is None) is allowed


def test_effect_identity_is_complete_and_deterministic() -> None:
    from tandem.domain.identity import EffectIdentity

    identity = EffectIdentity(
        institution_id="alpha",
        procedure_id="reg_e_dispute",
        case_id="D-IDENTITY",
        capability_id="core.post_provisional_credit",
        member_id="8830142",
        account_id="CHK-8830142-01",
        amount=Decimal("340.00"),
        currency="USD",
        business_reference="D-IDENTITY",
    )
    same = identity.model_copy()
    different_account = identity.model_copy(update={"account_id": "CHK-OTHER"})
    assert identity.idempotency_key == same.idempotency_key
    assert identity.idempotency_key != different_account.idempotency_key


def test_after_submit_unknown_is_not_generic_hard_failure() -> None:
    source = inspect.getsource(DeterministicExecutor.execute)
    assert "ExecutionPhase" in source
    assert "AFTER_SUBMIT_UNKNOWN" in source
    assert "POSSIBLY_APPLIED" in source or "UNCERTAIN_EFFECT" in source


def test_institution_routing_is_not_embedded_in_immutable_artifact() -> None:
    capability = load_capability_from_yaml(str(ARTIFACT))
    navigation_steps = [step for step in capability.steps if step.action.value == "NAVIGATE"]
    assert navigation_steps
    assert all("127.0.0.1" not in step.semantic_target for step in navigation_steps)
    assert "supported_surfaces" in CapabilityDefinition.model_fields
