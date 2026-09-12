"""Unit tests for domain models, capability schemas, and effect safety contracts."""

import pytest
from pydantic import ValidationError

from tandem.domain.capability import (
    CapabilityDefinition,
    ScopedGuardSpec,
    StepAction,
    StepDefinition,
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
from tandem.domain.outcomes import ExecutionOutcome, OutcomeCategory, OutcomeCode


def test_read_capability_does_not_require_commit_metadata():
    cap = CapabilityDefinition(
        id="core.lookup_transaction",
        version="1.0.0",
        name="Lookup Transaction",
        description="Reads member transactions",
        system="core_bank",
        effect=EffectSpec(effect_class=EffectClass.READ),
        input_schema={"type": "object", "properties": {"member_id": {"type": "string"}}},
        output_schema={"type": "object"},
        steps=[
            StepDefinition(
                step_id="step_1",
                action=StepAction.NAVIGATE,
                semantic_target="Search Page",
                locator_candidates=["#search_input"],
            )
        ],
    )
    assert cap.id == "core.lookup_transaction"
    assert cap.effect.effect_class == EffectClass.READ
    assert cap.compute_hash() is not None


def test_commit_without_precheck_rejected():
    with pytest.raises(ValidationError) as exc_info:
        CapabilityDefinition(
            id="core.post_credit_bad",
            name="Bad Credit",
            description="Missing precheck",
            system="core_bank",
            effect=EffectSpec(
                effect_class=EffectClass.COMMIT,
                idempotency_key="regE:{{case_id}}:credit",
                postcheck=PostcheckSpec(capability="core.read_memo"),
                reconciliation=ReconciliationSpec(),
                bounds=BoundsSpec(max_amount=500.0, currency="USD"),
            ),
            input_schema={"type": "object"},
            scoped_guard=ScopedGuardSpec(container_selector="#panel"),
        )
    assert "precheck" in str(exc_info.value)


def test_commit_without_postcheck_rejected():
    with pytest.raises(ValidationError) as exc_info:
        CapabilityDefinition(
            id="core.post_credit_bad",
            name="Bad Credit",
            description="Missing postcheck",
            system="core_bank",
            effect=EffectSpec(
                effect_class=EffectClass.COMMIT,
                idempotency_key="regE:{{case_id}}:credit",
                identity=EffectIdentitySpec(
                    institution_id="{{input.institution_id}}",
                    procedure_id="reg_e_dispute",
                    case_id="{{input.case_id}}",
                    capability_id="core.test",
                    member_id="{{input.member_id}}",
                    account_id="{{input.account_id}}",
                    amount="{{input.amount}}",
                    currency="{{input.currency}}",
                    business_reference="{{input.case_id}}",
                ),
                precheck=PrecheckSpec(capability="core.find_memo"),
                bounds=BoundsSpec(max_amount=500.0, currency="USD"),
            ),
            input_schema={"type": "object"},
            scoped_guard=ScopedGuardSpec(container_selector="#panel"),
        )
    assert "postcheck" in str(exc_info.value)


def test_commit_without_bounds_rejected():
    with pytest.raises(ValidationError) as exc_info:
        CapabilityDefinition(
            id="core.post_credit_bad",
            name="Bad Credit",
            description="Missing bounds",
            system="core_bank",
            effect=EffectSpec(
                effect_class=EffectClass.COMMIT,
                idempotency_key="regE:{{case_id}}:credit",
                precheck=PrecheckSpec(capability="core.find_memo"),
                postcheck=PostcheckSpec(capability="core.read_memo"),
            ),
            input_schema={"type": "object"},
            scoped_guard=ScopedGuardSpec(container_selector="#panel"),
        )
    assert "bounds" in str(exc_info.value)


def test_commit_without_scoped_guard_rejected():
    with pytest.raises(ValidationError) as exc_info:
        CapabilityDefinition(
            id="core.post_credit_bad",
            name="Bad Credit",
            description="Missing scoped guard",
            system="core_bank",
            effect=EffectSpec(
                effect_class=EffectClass.COMMIT,
                idempotency_key="regE:{{case_id}}:credit",
                identity=EffectIdentitySpec(
                    institution_id="{{input.institution_id}}",
                    procedure_id="reg_e_dispute",
                    case_id="{{input.case_id}}",
                    capability_id="core.test",
                    member_id="{{input.member_id}}",
                    account_id="{{input.account_id}}",
                    amount="{{input.amount}}",
                    currency="{{input.currency}}",
                    business_reference="{{input.case_id}}",
                ),
                precheck=PrecheckSpec(capability="core.find_memo"),
                postcheck=PostcheckSpec(capability="core.read_memo"),
                reconciliation=ReconciliationSpec(),
                bounds=BoundsSpec(max_amount=500.0, currency="USD"),
            ),
            input_schema={"type": "object"},
            # Missing scoped_guard
        )
    assert "scoped_guard" in str(exc_info.value)


def test_valid_commit_capability_accepted_and_hashed():
    cap = CapabilityDefinition(
        id="core.post_provisional_credit",
        version="1.0.0",
        name="Post Provisional Credit",
        description="Posts Regulation E provisional credit to member account",
        system="core_bank",
        effect=EffectSpec(
            effect_class=EffectClass.COMMIT,
            idempotency_key="regE:{{case_id}}:provisional_credit",
            identity=EffectIdentitySpec(
                institution_id="{{input.institution_id}}",
                procedure_id="reg_e_dispute",
                case_id="{{input.case_id}}",
                capability_id="core.post_provisional_credit",
                member_id="{{input.member_id}}",
                account_id="{{input.account_id}}",
                amount="{{input.amount}}",
                currency="{{input.currency}}",
                business_reference="{{input.case_id}}",
            ),
            precheck=PrecheckSpec(
                capability="core.find_memo_by_case",
                params={"case_id": "{{input.case_id}}"},
                if_found="ALREADY_APPLIED",
            ),
            postcheck=PostcheckSpec(
                capability="core.find_memo_by_case",
                params={"case_id": "{{input.case_id}}"},
                expected_status="CONFIRMED",
            ),
            reconciliation=ReconciliationSpec(),
            compensation="core.reverse_provisional_credit",
            bounds=BoundsSpec(max_amount=500.00, currency="USD"),
        ),
        input_schema={
            "type": "object",
            "properties": {
                "member_id": {"type": "string"},
                "case_id": {"type": "string"},
                "amount": {"type": "number"},
            },
            "required": ["member_id", "case_id", "amount"],
        },
        scoped_guard=ScopedGuardSpec(
            guard_id="credit_commit_guard",
            container_selector="#credit_scope_container",
            expected_member_template="{{input.member_id}}",
            expected_amount_template="{{input.amount}}",
            expected_case_template="{{input.case_id}}",
        ),
        steps=[
            StepDefinition(
                step_id="step_commit_click",
                action=StepAction.SUBMIT,
                semantic_target="Arbitrary human label",
                locator_candidates=[".btn-commit-final", "#btn_commit"],
                guard_ref="credit_commit_guard",
            )
        ],
    )
    assert cap.id == "core.post_provisional_credit"
    digest = cap.compute_hash()
    assert isinstance(digest, str)
    assert len(digest) == 64  # SHA-256 hex string


def test_outcome_properties():
    succ = ExecutionOutcome(
        category=OutcomeCategory.SUCCESS,
        code=OutcomeCode.COMPLETED,
        message="Done",
        money_moved=True,
    )
    assert succ.is_success
    assert not succ.is_retryable
    assert not succ.requires_human

    biz = ExecutionOutcome(
        category=OutcomeCategory.BUSINESS_OUTCOME,
        code=OutcomeCode.ALREADY_APPLIED,
        message="Already credited",
    )
    assert biz.is_business_terminal
    assert not biz.is_retryable

    rec = ExecutionOutcome(
        category=OutcomeCategory.RECOVERABLE_FAILURE,
        code=OutcomeCode.SESSION_EXPIRED,
        message="Session timed out",
    )
    assert rec.is_retryable
    assert not rec.requires_human

    uncertain = ExecutionOutcome(
        category=OutcomeCategory.UNCERTAIN_EFFECT,
        code=OutcomeCode.UNCERTAIN_EFFECT,
        message="Connection dropped before confirmation",
    )
    assert not uncertain.is_retryable  # Critical: never auto-retry uncertain effect!
    assert uncertain.requires_human
