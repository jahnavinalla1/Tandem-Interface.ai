"""Effect-aware capability execution engine with atomic COMMIT admission."""

import os
from typing import Any, Dict, Optional
from uuid import uuid4

from playwright.sync_api import Page
from sqlalchemy.orm import Session

from tandem.domain.capability import CapabilityDefinition
from tandem.domain.effects import EffectClaimStatus, EffectClass
from tandem.domain.identity import EffectIdentity
from tandem.domain.money import parse_money
from tandem.domain.outcomes import ExecutionOutcome, ExecutionPhase, OutcomeCategory, OutcomeCode
from tandem.ledger.repository import LedgerRepository
from tandem.policy.engine import PolicyEngine
from tandem.replay.crash_injection import maybe_crash
from tandem.replay.executor import DeterministicExecutor, render_template
from tandem.replay.http_executor import execute_http_commit
from tandem.replay.precheck import execute_precheck
from tandem.replay.reconciliation import reconcile_commit_execution
from tandem.surfaces.base import SurfaceOverlay


def settle_claim_status(outcome: ExecutionOutcome) -> EffectClaimStatus:
    """Map a COMMIT outcome onto the durable claim lifecycle.

    Only a target-confirmed effect settles as APPLIED. Anything that may have
    reached the target without proof stays UNCERTAIN and fences every later
    attempt. Halts that provably happened before the submit -- a recoverable
    transport/session failure, a human interstitial, or a target that
    positively reports absence -- leave the claim retryable so the same effect
    can be re-admitted (with a new fencing token) once the condition clears.
    Guard and policy violations are permanent: repeating them blindly would
    repeat the mismatch.
    """
    if outcome.is_success:
        return EffectClaimStatus.APPLIED
    if outcome.category == OutcomeCategory.UNCERTAIN_EFFECT:
        return EffectClaimStatus.UNCERTAIN
    if outcome.code == OutcomeCode.CONFIRMED_NOT_APPLIED:
        # The target's own inquiry positively reported absence after the
        # interrupted submit; that proof, not the transport, decides.
        return EffectClaimStatus.FAILED_RETRYABLE
    if outcome.execution_phase != ExecutionPhase.BEFORE_SUBMIT:
        # Any other non-confirmed outcome after the submit began is, by
        # definition, not provably absent.
        return EffectClaimStatus.UNCERTAIN
    if outcome.category in {OutcomeCategory.RECOVERABLE_FAILURE, OutcomeCategory.NEEDS_HUMAN}:
        return EffectClaimStatus.FAILED_RETRYABLE
    return EffectClaimStatus.FAILED_PERMANENT


class EffectEngine:
    """Orchestrates effect-aware replay with prechecks, scoped guards, and reconciliation."""

    def __init__(
        self,
        session: Session,
        page: Page,
        overlay: Optional[SurfaceOverlay] = None,
        browser_session_id: str | None = None,
    ):
        self.session = session
        self.page = page
        self.overlay = overlay
        self.repo = LedgerRepository(session)
        self.executor = DeterministicExecutor(page=page, overlay=overlay)
        self.owner_id = f"AUTOMATION:{os.getpid()}:{uuid4().hex}"
        self.browser_session_id = browser_session_id

    def execute_capability(
        self, capability: CapabilityDefinition, inputs: Dict[str, Any]
    ) -> ExecutionOutcome:
        """Execute a capability according to its declared effect class."""
        inputs = dict(inputs)
        case_id = str(inputs.get("case_id", "UNKNOWN_CASE"))
        member_id = str(inputs.get("member_id", "UNKNOWN_MEMBER"))
        amount = parse_money(inputs.get("amount", "0.00"))
        inputs["amount"] = amount
        inputs.setdefault("institution_id", self.overlay.institution_id if self.overlay else "alpha")
        inputs.setdefault("currency", capability.effect.bounds.currency if capability.effect.bounds else "USD")

        if capability.effect.effect_class == EffectClass.COMMIT and not inputs.get("account_id"):
            if capability.system != "core_bank":
                return ExecutionOutcome(
                    category=OutcomeCategory.HARD_FAILURE,
                    code=OutcomeCode.ENTITY_BINDING_MISMATCH,
                    message="Complete effect identity requires account_id before COMMIT",
                    money_moved=False,
                )
            try:
                import httpx

                from tandem.surfaces.routing import core_bank_url_for

                response = httpx.get(
                    f"{core_bank_url_for(inputs.get('institution_id'))}/api/member/{member_id}",
                    timeout=3.0,
                )
                response.raise_for_status()
                member_data = response.json()
                if str(member_data.get("member_id", "")) != member_id:
                    raise ValueError("member identity response mismatch")
                inputs["account_id"] = str(member_data["account_id"])
            except Exception as exc:
                return ExecutionOutcome(
                    category=OutcomeCategory.HARD_FAILURE,
                    code=OutcomeCode.PRECHECK_UNAVAILABLE,
                    message=f"Unable to establish complete effect identity: {exc}",
                    money_moved=False,
                )

        identity = (
            EffectIdentity.from_capability(capability, inputs)
            if capability.effect.effect_class == EffectClass.COMMIT
            else None
        )

        # Ensure case exists in ledger
        self.repo.create_or_get_case(
            case_id=case_id,
            member_id=member_id,
            amount=amount,
            currency=str(inputs.get("currency", "USD")),
        )

        # -------------------------------------------------------------------
        # 1. Policy Evaluation
        # -------------------------------------------------------------------
        policy_failure = PolicyEngine.evaluate(capability, inputs)
        if policy_failure:
            self.repo.record_event(
                case_id=case_id,
                event_type="POLICY_DENIED",
                step_name=capability.id,
                payload={"message": policy_failure.message},
            )
            return policy_failure

        # -------------------------------------------------------------------
        # 2. Idempotency Key Computation
        # -------------------------------------------------------------------
        idempotency_key = identity.idempotency_key if identity else None
        if not idempotency_key and capability.effect.idempotency_key:
            idempotency_key = render_template(capability.effect.idempotency_key, {"input": inputs})

        claim = None
        if capability.effect.effect_class == EffectClass.COMMIT:
            if identity is None or idempotency_key is None:
                raise RuntimeError("COMMIT execution reached admission without an effect identity")
            claim = self.repo.claim_effect(identity, self.owner_id)
            if not claim.acquired:
                self.repo.record_event(
                    case_id=case_id,
                    event_type="EFFECT_CLAIM_REJECTED",
                    step_name=capability.id,
                    payload={
                        "idempotency_key": idempotency_key,
                        "owner_id": claim.owner_id,
                        "fencing_token": claim.fencing_token,
                        "status": claim.status,
                    },
                )
            self.session.commit()
            maybe_crash("C_AFTER_CLAIM", capability.id)
            if not claim.acquired:
                code = OutcomeCode(claim.status)
                if code == OutcomeCode.ALREADY_APPLIED:
                    confirmed = execute_precheck(capability, inputs)
                    if confirmed.code == OutcomeCode.ALREADY_APPLIED:
                        return confirmed
                return ExecutionOutcome(
                    category=OutcomeCategory.BUSINESS_OUTCOME,
                    code=code,
                    message=(
                        f"Effect admission rejected for '{capability.id}': {claim.status}. "
                        "This worker did not reach the external mutation."
                    ),
                    details={
                        "idempotency_key": idempotency_key,
                        "fencing_token": claim.fencing_token,
                    },
                    money_moved=False,
                )

        # -------------------------------------------------------------------
        # 3. Positive target precheck while holding the durable effect claim
        # -------------------------------------------------------------------
        if capability.effect.effect_class == EffectClass.COMMIT:
            precheck_outcome = execute_precheck(capability, inputs)
            if precheck_outcome.code == OutcomeCode.ALREADY_APPLIED:
                # Effect already exists! Record to ledger and return immediately without firing browser steps.
                self.repo.record_event(
                    case_id=case_id,
                    event_type="PRECHECK_ALREADY_APPLIED",
                    step_name=capability.id,
                    payload={
                        "memo": precheck_outcome.audit_ref,
                        "message": precheck_outcome.message,
                    },
                )
                exec_record = self.repo.start_execution(
                    case_id=case_id,
                    capability_id=capability.id,
                    capability_version=capability.version,
                    effect_class=capability.effect.effect_class.value,
                    idempotency_key=idempotency_key,
                )
                self.repo.complete_execution(
                    execution_id=exec_record.id,
                    status=OutcomeCode.ALREADY_APPLIED.value,
                    audit_ref=precheck_outcome.audit_ref,
                    money_moved=False,
                )
                if claim and idempotency_key:
                    self.repo.transition_effect_claim(
                        idempotency_key,
                        self.owner_id,
                        claim.fencing_token,
                        {EffectClaimStatus.CLAIMED},
                        EffectClaimStatus.APPLIED,
                    )
                self.session.commit()
                return precheck_outcome
            if precheck_outcome.code != OutcomeCode.NOT_APPLIED:
                if claim and idempotency_key:
                    self.repo.transition_effect_claim(
                        idempotency_key,
                        self.owner_id,
                        claim.fencing_token,
                        {EffectClaimStatus.CLAIMED},
                        EffectClaimStatus.FAILED_RETRYABLE,
                    )
                self.repo.record_event(
                    case_id=case_id,
                    event_type="PRECHECK_HALTED",
                    step_name=capability.id,
                    payload={
                        "code": precheck_outcome.code.value,
                        "message": precheck_outcome.message,
                    },
                )
                self.session.commit()
                return precheck_outcome
            maybe_crash("D_AFTER_PRECHECK", capability.id)

            assert idempotency_key is not None
            self.repo.stage_intent(case_id, idempotency_key, capability.id)
            self.repo.record_event(
                case_id=case_id,
                event_type="EFFECT_INTENT_STAGED",
                step_name=capability.id,
                payload={"idempotency_key": idempotency_key},
            )
            self.session.commit()

        # -------------------------------------------------------------------
        # 4. Acquire browser-session lease and durably enter APPLYING
        # -------------------------------------------------------------------
        lease = self.repo.acquire_lease(
            case_id=case_id,
            owner=self.owner_id,
            owner_type="AUTOMATION",
        )
        lease_token = lease.fencing_token
        if claim and idempotency_key:
            transitioned = self.repo.transition_effect_claim(
                idempotency_key,
                self.owner_id,
                claim.fencing_token,
                {EffectClaimStatus.CLAIMED},
                EffectClaimStatus.APPLYING,
            )
            if not transitioned:
                raise RuntimeError("Effect claim fencing token became stale before APPLYING")

        exec_record = self.repo.start_execution(
            case_id=case_id,
            capability_id=capability.id,
            capability_version=capability.version,
            effect_class=capability.effect.effect_class.value,
            idempotency_key=idempotency_key,
            expected_entity=member_id,
            expected_amount=amount,
            browser_session_id=self.browser_session_id,
        )
        self.session.commit()

        def maintain_action_lease() -> bool:
            self.repo.heartbeat_lease(case_id, self.owner_id, lease_token)
            self.session.commit()
            return True

        self.executor.lease_validator = maintain_action_lease

        # -------------------------------------------------------------------
        # 5. Deterministic Browser Replay (Includes Control-Scoped Guards)
        # -------------------------------------------------------------------
        try:
            if any(step.action.value == "HTTP_POST" for step in capability.steps):
                maintain_action_lease()
                outcome = execute_http_commit(capability, inputs)
            else:
                outcome = self.executor.execute(capability=capability, inputs=inputs)
            if capability.effect.effect_class == EffectClass.COMMIT and (
                outcome.code == OutcomeCode.POSSIBLY_APPLIED
                or outcome.execution_phase == ExecutionPhase.AFTER_SUBMIT_UNKNOWN
            ):
                outcome = reconcile_commit_execution(
                    capability=capability,
                    inputs=inputs,
                    error_message=outcome.message,
                )
        except Exception as exc:
            # Network drop or browser crash around commit -> invoke reconciliation!
            if capability.effect.effect_class == EffectClass.COMMIT:
                outcome = reconcile_commit_execution(
                    capability=capability, inputs=inputs, error_message=str(exc)
                )
            else:
                outcome = ExecutionOutcome(
                    category=OutcomeCategory.RECOVERABLE_FAILURE,
                    code=OutcomeCode.NETWORK_TIMEOUT,
                    message=f"Browser execution failed: {exc}",
                    money_moved=False,
                )

        # -------------------------------------------------------------------
        # 6. Post-Action Settlement, Audit Persist & Lease Release
        # -------------------------------------------------------------------
        maybe_crash("J_BEFORE_FINAL_LEDGER_EVENT", capability.id)
        self.repo.complete_execution(
            execution_id=exec_record.id,
            status=outcome.code.value,
            observed_entity=inputs.get("member_id"),
            observed_amount=amount if outcome.money_moved else None,
            audit_ref=outcome.audit_ref,
            money_moved=outcome.money_moved,
            failure_category=outcome.category.value if not outcome.is_success else None,
        )

        if outcome.money_moved:
            self.repo.update_case_status(
                case_id=case_id,
                status="PROVISIONAL_CREDIT_POSTED",
                money_moved=True,
            )
            self.repo.record_event(
                case_id=case_id,
                event_type="MONEY_MOVED",
                step_name=capability.id,
                payload={"amount": amount, "ref": outcome.audit_ref},
            )

        if outcome.code == OutcomeCode.CONFIRMED_APPLIED:
            self.repo.record_event(
                case_id=case_id,
                event_type="EFFECT_RECONCILED",
                step_name=capability.id,
                payload={"audit_ref": outcome.audit_ref},
            )

        if outcome.category == OutcomeCategory.UNCERTAIN_EFFECT:
            self.repo.update_case_status(case_id=case_id, status="UNCERTAIN_EFFECT")
            self.repo.record_event(
                case_id=case_id,
                event_type="UNCERTAIN_EFFECT_ESCALATION",
                step_name=capability.id,
                payload={"message": outcome.message},
            )

        elif outcome.category == OutcomeCategory.NEEDS_HUMAN:
            self.repo.update_case_status(case_id=case_id, status="NEEDS_HUMAN")
            self.repo.record_event(
                case_id=case_id,
                event_type="HUMAN_HANDOFF_REQUIRED",
                step_name=capability.id,
                payload={"code": outcome.code.value, "message": outcome.message},
            )

        elif outcome.category == OutcomeCategory.HARD_FAILURE:
            self.repo.update_case_status(case_id=case_id, status="FAILED")
            self.repo.record_event(
                case_id=case_id,
                event_type="HARD_FAILURE",
                step_name=capability.id,
                payload={"code": outcome.code.value, "message": outcome.message},
            )

        if claim and idempotency_key:
            target_status = settle_claim_status(outcome)
            transitioned = self.repo.transition_effect_claim(
                idempotency_key,
                self.owner_id,
                claim.fencing_token,
                {EffectClaimStatus.APPLYING},
                target_status,
            )
            if not transitioned:
                raise RuntimeError("Effect claim fencing token became stale before settlement")
            if outcome.is_success:
                self.repo.mark_intent_committed(idempotency_key)

        self.repo.release_lease(
            case_id=case_id,
            owner_id=self.owner_id,
            fencing_token=lease_token,
        )
        self.session.commit()

        return outcome
