"""Deterministic capability execution engine using Playwright and Surface abstraction."""

from typing import Any, Callable, Dict, Optional

from playwright.sync_api import Page

from tandem.config import settings
from tandem.domain.capability import CapabilityDefinition, StepAction
from tandem.domain.effects import EffectClass
from tandem.domain.errors import (
    AmountMismatchError,
    ComplianceInterstitialError,
    EntityBindingMismatchError,
    LeaseConflictError,
    PageDriftError,
    SessionExpiredError,
)
from tandem.domain.outcomes import ExecutionOutcome, ExecutionPhase, OutcomeCategory, OutcomeCode
from tandem.policy.telemetry import llm_tracker
from tandem.replay.crash_injection import maybe_crash
from tandem.replay.guards import verify_control_scoped_guard
from tandem.surfaces.base import SurfaceOverlay
from tandem.surfaces.playwright_surface import PlaywrightSurface
from tandem.surfaces.routing import core_bank_url_for


def render_template(template_str: str, context: Dict[str, Any]) -> str:
    """Simple template renderer resolving expressions like {{input.member_id}}."""
    result = template_str
    for key, val in context.get("input", {}).items():
        result = result.replace(f"{{{{input.{key}}}}}", str(val))
        result = result.replace(f"{{{{{key}}}}}", str(val))
    return result


class DeterministicExecutor:
    """Executes a compiled capability artifact deterministically using Playwright with 0 LLM calls."""

    def __init__(self, page: Page, overlay: Optional[SurfaceOverlay] = None):
        self.page = page
        self.surface = PlaywrightSurface(page)
        self.overlay = overlay
        self.lease_validator: Callable[[], bool] | None = None

    def execute(self, capability: CapabilityDefinition, inputs: Dict[str, Any]) -> ExecutionOutcome:
        """Execute capability steps. Replay must perform ZERO LLM calls."""
        # Baseline LLM count check
        llm_count_before = llm_tracker.call_count

        context = {"input": inputs}
        frame_selector = "#core_workspace_frame"  # Standard hostile frame if applicable
        execution_phase = ExecutionPhase.BEFORE_SUBMIT

        def mark_submit_initiated() -> None:
            nonlocal execution_phase
            execution_phase = ExecutionPhase.SUBMIT_INITIATED
            maybe_crash("F_BEFORE_SUBMIT", capability.id)

        try:
            # Check for session expiration early if page loaded
            if "SESSION EXPIRED" in self.page.content():
                raise SessionExpiredError("Target system session has timed out")

            for step in capability.steps:
                if self.lease_validator is not None and not self.lease_validator():
                    raise LeaseConflictError(
                        "Browser action rejected because the ownership fencing token is stale"
                    )
                # Check for compliance review interstitial
                try:
                    ctx = self.surface._get_context(frame_selector)
                    if (
                        ctx.locator("#compliance_interstitial_panel").count() > 0
                        or "COMPLIANCE INTERSTITIAL REVIEW REQUIRED" in (self.page.content() or "")
                    ):
                        raise ComplianceInterstitialError(
                            "Compliance review interstitial encountered; manual operator sign-off required"
                        )
                except ComplianceInterstitialError:
                    raise
                except Exception:
                    pass

                # 1. Container-scoped guard check immediately prior to or during commit actions
                if step.action in {StepAction.ASSERT_CONTAINER, StepAction.SUBMIT}:
                    verify_control_scoped_guard(
                        capability=capability,
                        inputs=inputs,
                        surface=self.surface,
                        frame_selector=step.frame_selector or frame_selector,
                        overlay=self.overlay,
                        control_candidates=(
                            step.locator_candidates if step.action == StepAction.SUBMIT else None
                        ),
                        semantic_target=step.semantic_target,
                    )
                    if step.action == StepAction.SUBMIT:
                        maybe_crash("E_AFTER_GUARD", capability.id)

                # 2. Execute step action
                if step.action == StepAction.NAVIGATE:
                    url = render_template(step.semantic_target, context)
                    if url.startswith("surface://"):
                        if url != f"surface://{capability.system}/home":
                            raise ValueError(f"Unsupported logical surface route: {url}")
                        routes = {
                            "core_bank": core_bank_url_for(inputs.get("institution_id")),
                            "processor": settings.processor_url,
                            "documents": settings.documents_url,
                        }
                        if capability.system not in routes:
                            raise ValueError(
                                f"No runtime route configured for surface '{capability.system}'"
                            )
                        url = routes[capability.system]
                    self.surface.navigate(url)

                elif step.action == StepAction.FILL:
                    value = (
                        render_template(step.input_value_template, context)
                        if step.input_value_template
                        else ""
                    )
                    effective_frame = step.frame_selector or frame_selector
                    self.surface.resolve_and_fill(
                        semantic_target=step.semantic_target,
                        candidates=step.locator_candidates,
                        value=value,
                        frame_selector=effective_frame,
                        overlay=self.overlay,
                    )

                elif step.action in {StepAction.CLICK, StepAction.SUBMIT}:
                    effective_frame = step.frame_selector or frame_selector
                    self.surface.resolve_and_click(
                        semantic_target=step.semantic_target,
                        candidates=step.locator_candidates,
                        frame_selector=effective_frame,
                        overlay=self.overlay,
                        before_click=(
                            mark_submit_initiated if step.action == StepAction.SUBMIT else None
                        ),
                    )
                    if step.action == StepAction.SUBMIT:
                        execution_phase = ExecutionPhase.SUBMIT_CONFIRMED
                        maybe_crash("G_AFTER_TARGET_ACCEPTS", capability.id)

            # Invariant check: Assert ZERO LLM calls took place during replay
            llm_calls_made = llm_tracker.call_count - llm_count_before
            if llm_calls_made > 0:
                raise RuntimeError(
                    f"CRITICAL SAFETY VIOLATION: Replay engine invoked {llm_calls_made} LLM calls! "
                    f"Replay must be 100% deterministic."
                )

            context_el = self.surface._get_context(frame_selector)
            memo_code = None
            try:
                memo_el = context_el.locator("#receipt_memo_code, .result-memo-code").first
                if memo_el.is_visible(timeout=2000):
                    memo_code = memo_el.text_content().strip()
            except Exception:
                pass

            money_moved = False
            try:
                money_el = context_el.locator("#receipt_money_moved").first
                if money_el.is_visible(timeout=1000):
                    money_moved = "MONEY_MOVED=TRUE" in (money_el.text_content() or "")
            except Exception:
                pass

            # Every COMMIT requires independent target confirmation. DOM receipts
            # are evidence, but never sufficient proof of an external effect.
            if capability.effect.effect_class == EffectClass.COMMIT:
                from tandem.replay.postcheck import execute_postcheck

                maybe_crash("H_BEFORE_POSTCHECK", capability.id)
                postcheck = execute_postcheck(capability, inputs)
                maybe_crash("I_AFTER_POSTCHECK", capability.id)
                if not postcheck.is_success:
                    return postcheck.model_copy(
                        update={"execution_phase": ExecutionPhase.AFTER_SUBMIT_UNKNOWN}
                    )
                memo_code = postcheck.audit_ref
                money_moved = postcheck.money_moved

            return ExecutionOutcome(
                category=OutcomeCategory.SUCCESS,
                code=OutcomeCode.COMPLETED,
                message=f"Capability '{capability.id}' replayed successfully with 0 LLM calls",
                details={
                    "memo_code": memo_code,
                    "money_moved": money_moved,
                    "drift_events": self.surface.drift_events,
                },
                money_moved=money_moved,
                audit_ref=memo_code,
                execution_phase=execution_phase,
            )

        except EntityBindingMismatchError as e:
            return ExecutionOutcome(
                category=OutcomeCategory.HARD_FAILURE,
                code=OutcomeCode.ENTITY_BINDING_MISMATCH,
                message=str(e),
                details={"inputs": inputs},
                money_moved=False,
                execution_phase=execution_phase,
            )

        except AmountMismatchError as e:
            return ExecutionOutcome(
                category=OutcomeCategory.HARD_FAILURE,
                code=OutcomeCode.AMOUNT_MISMATCH,
                message=str(e),
                details={"inputs": inputs},
                money_moved=False,
                execution_phase=execution_phase,
            )

        except PageDriftError as e:
            return ExecutionOutcome(
                category=OutcomeCategory.RECOVERABLE_FAILURE,
                code=OutcomeCode.PAGE_DRIFT,
                message=str(e),
                details={"drift_events": self.surface.drift_events},
                money_moved=False,
                execution_phase=execution_phase,
            )

        except ComplianceInterstitialError as e:
            return ExecutionOutcome(
                category=OutcomeCategory.NEEDS_HUMAN,
                code=OutcomeCode.COMPLIANCE_INTERSTITIAL,
                message=str(e),
                details={"interstitial_type": "REG_E_COMPLIANCE_REVIEW"},
                money_moved=False,
                execution_phase=execution_phase,
            )

        except SessionExpiredError as e:
            return ExecutionOutcome(
                category=OutcomeCategory.RECOVERABLE_FAILURE,
                code=OutcomeCode.SESSION_EXPIRED,
                message=str(e),
                money_moved=False,
                execution_phase=execution_phase,
            )

        except LeaseConflictError as e:
            return ExecutionOutcome(
                category=OutcomeCategory.NEEDS_HUMAN,
                code=OutcomeCode.LEASE_FENCED,
                message=str(e),
                money_moved=False,
                execution_phase=execution_phase,
            )

        except Exception as e:
            if capability.effect.effect_class == EffectClass.COMMIT and execution_phase in {
                ExecutionPhase.SUBMIT_INITIATED,
                ExecutionPhase.SUBMIT_CONFIRMED,
            }:
                return ExecutionOutcome(
                    category=OutcomeCategory.UNCERTAIN_EFFECT,
                    code=OutcomeCode.POSSIBLY_APPLIED,
                    message=f"Browser failed after irreversible submit began: {e}",
                    details={"original_error": str(e)},
                    money_moved=False,
                    execution_phase=ExecutionPhase.AFTER_SUBMIT_UNKNOWN,
                )
            return ExecutionOutcome(
                category=OutcomeCategory.RECOVERABLE_FAILURE,
                code=OutcomeCode.NETWORK_TIMEOUT,
                message=f"Replay failed before irreversible submit: {e}",
                money_moved=False,
                execution_phase=ExecutionPhase.BEFORE_SUBMIT,
            )
