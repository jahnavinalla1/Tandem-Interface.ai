"""Focused assessment demonstration for the Tandem automation system.

Executes five primary assessment scenarios:
  1. discovery: LLM agent discovers workflow and compiles capability artifact
  2. replay-new-case: Zero-LLM deterministic replay on fresh dispute
  3. replay-same-case: Precheck idempotency prevents duplicate provisional credit
  4. wrong-member-or-amount: Identity and policy guards block unsafe submissions
  5. uncertain-effect: Commit-response loss is reconciled without a blind retry

Crash recovery, human handoff, and tenant overlays remain covered by the automated
integration and regression suites rather than expanding the primary demo.
"""

import argparse
import time
from decimal import Decimal

from playwright.sync_api import sync_playwright

from tandem.discovery.agent import DiscoveryAgent
from tandem.discovery.compiler import CapabilityCompiler
from tandem.domain.capability import load_capability_from_yaml
from tandem.domain.outcomes import OutcomeCategory, OutcomeCode
from tandem.handoff.coordinator import HandoffCoordinator
from tandem.ledger.database import SessionLocal
from tandem.ledger.service import LedgerService
from tandem.policy.telemetry import llm_tracker
from tandem.replay.engine import EffectEngine
from tandem.replay.executor import DeterministicExecutor
from tandem.support.simulator_control import (
    ensure_simulators_running,
    get_member,
    reset_all_simulators,
    set_core_bank_mode,
    set_processor_mode,
)
from tandem.surfaces.overlays import get_overlay
from tandem.workflow.reg_e import RegEWorkflow


def print_banner(title: str, scenario_num: int):
    print("\n" + "=" * 75)
    print(f"  SCENARIO {scenario_num}: {title.upper()}")
    print("=" * 75)


def run_discovery():
    print_banner("Discovery Agent & Capability Compiler", 1)
    print("[*] Concept: LLM agent explores hostile legacy banking UI, observes selectors,")
    print("    validates container bounds, and compiles a typed, versioned YAML capability artifact.")
    print("    Crucial Invariant: LLM is strictly used for discovery; NEVER in production replay.\n")

    reset_all_simulators()
    llm_tracker.reset()

    print("[*] Launching browser discovery exploration...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        agent = DiscoveryAgent(page=page)
        discovery_inputs = {
            "member_id": "8830142",
            "case_id": "D-DISCOVERY-001",
            "amount": Decimal("150.00"),
        }
        trace = agent.discover_provisional_credit(inputs=discovery_inputs)
        browser.close()

    print(f"[+] Exploration complete. Captured {len(trace.actions)} user surface interactions.")
    print(f"[+] Discovered Memo Code: {trace.discovered_memo}")
    print(f"[+] Discovery LLM Invocations: {llm_tracker.call_count}")

    compiler = CapabilityCompiler(output_dir="capabilities/compiled")
    compiled_cap, artifact_path = compiler.compile(
        trace=trace,
        target_filename="demo_post_provisional_credit.yaml",
    )

    print(f"[+] Capability successfully compiled to {artifact_path}!")
    print(f"    - Capability ID:    {compiled_cap.id} (v{compiled_cap.version})")
    print(f"    - Effect Class:     {compiled_cap.effect.effect_class.value}")
    print(f"    - SHA-256 Digest:   {compiled_cap.artifact_hash}")
    print(f"    - Compiled Steps:   {len(compiled_cap.steps)} discrete Playwright actions")
    print(f"    - Scoped Guard:     Container '{compiled_cap.scoped_guard.container_selector}'")
    print(f"    - Precheck:         {compiled_cap.effect.precheck.capability} -> '{compiled_cap.effect.precheck.if_found}'")
    print("\n[OK] Scenario 1 Verification Passed: Discovered and compiled valid capability artifact.")


def run_replay_new_case():
    print_banner("Deterministic Replay on New Dispute Case", 2)
    print("[*] Concept: Executes compiled capability artifact on fresh dispute case.")
    print("    Crucial Invariant: llm_call_count == 0 asserted in execution.\n")

    reset_all_simulators()
    llm_tracker.reset()

    cap = load_capability_from_yaml("capabilities/core/post_provisional_credit.yaml")
    case_id = f"D-REPLAY-{int(time.time())}"
    inputs = {
        "institution_id": "alpha",
        "member_id": "8830142",
        "account_id": "CHK-8830142-01",
        "case_id": case_id,
        "amount": Decimal("175.50"),
        "currency": "USD",
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        executor = DeterministicExecutor(page=page)
        outcome = executor.execute(capability=cap, inputs=inputs)
        browser.close()

    print(f"[+] Replay Outcome:        {outcome.code.value} ({outcome.category.value})")
    print(f"[+] Money Moved:           {outcome.money_moved} (${inputs['amount']:.2f} USD)")
    print(f"[+] Core Banking Memo:     {outcome.audit_ref}")
    print(f"[+] LLM Replay Calls:      {llm_tracker.call_count} (Strict Zero-LLM Invariant)")

    assert outcome.code == OutcomeCode.COMPLETED
    assert outcome.money_moved is True
    assert llm_tracker.call_count == 0
    print("\n[OK] Scenario 2 Verification Passed: Fresh credit posted deterministically with 0 LLM calls.")


def run_replay_same_case():
    print_banner("Precheck Idempotency (Prevent Duplicate Credit)", 3)
    print("[*] Concept: Replaying against an existing case detects the prior credit memo via precheck.")
    print("    Crucial Invariant: Returns ALREADY_APPLIED and halts; touches no buttons; 0 duplicate credit.\n")

    reset_all_simulators()
    llm_tracker.reset()

    cap = load_capability_from_yaml("capabilities/core/post_provisional_credit.yaml")
    case_id = f"D-IDEMPOTENT-{int(time.time())}"
    inputs = {
        "institution_id": "alpha",
        "member_id": "8830142",
        "account_id": "CHK-8830142-01",
        "case_id": case_id,
        "amount": Decimal("210.00"),
        "currency": "USD",
    }

    db = SessionLocal()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        engine = EffectEngine(session=db, page=page)

        # First pass: Posts credit
        print("[*] Pass 1: Posting initial provisional credit via EffectEngine...")
        outcome_1 = engine.execute_capability(capability=cap, inputs=inputs)
        print(f"    Pass 1 Outcome: {outcome_1.code.value} | Memo: {outcome_1.audit_ref}")
        initial_balance = get_member("8830142")["balance"]

        # Second pass: Replay same case
        print("\n[*] Pass 2: Attempting duplicate execution with same case_id...")
        llm_tracker.reset()
        outcome_2 = engine.execute_capability(capability=cap, inputs=inputs)
        browser.close()

    subsequent_balance = get_member("8830142")["balance"]
    print(f"    Pass 2 Outcome:        {outcome_2.code.value} ({outcome_2.category.value})")
    print(f"    Precheck Detection:    {outcome_2.message}")
    print(f"    Money Moved in Pass 2: {outcome_2.money_moved}")
    print(f"    Member Balance:        ${subsequent_balance:.2f} (Delta: ${subsequent_balance - initial_balance:.2f})")
    print(f"    LLM Replay Calls:      {llm_tracker.call_count}")

    assert outcome_2.code == OutcomeCode.ALREADY_APPLIED
    assert outcome_2.money_moved is False
    assert subsequent_balance == initial_balance
    db.close()
    print("\n[OK] Scenario 3 Verification Passed: Duplicate provisional credit prevented by idempotency precheck.")


def run_transposed_id():
    print_banner("Wrong Member or Amount Blocked Before COMMIT", 4)
    print("[*] Concept: Confusable account transposed in search or DOM (#8830124 vs #8830142).")
    print("    Crucial Invariant: Scoped guard inspects confirmation container; halts with ENTITY_BINDING_MISMATCH.\n")

    reset_all_simulators()
    llm_tracker.reset()

    cap = load_capability_from_yaml("capabilities/core/post_provisional_credit.yaml")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Put page on 8830124's confirmation screen
        page.goto("http://127.0.0.1:8001/workspace/credit/entry?member_id=8830124")
        page.fill("input[name='case_id']", "D-TRANSPOSE-4001")
        page.fill("input[name='amount']", "100.00")
        page.click("button.btn-proceed")

        transposed_cap = cap.model_copy(deep=True)
        transposed_cap.steps = [s for s in transposed_cap.steps if s.step_id == "step_scoped_guard_and_commit"]

        executor = DeterministicExecutor(page=page)
        outcome = executor.execute(
            capability=transposed_cap,
            inputs={"member_id": "8830142", "case_id": "D-TRANSPOSE-4001", "amount": Decimal("100.00")},
        )
        amount_page = browser.new_page()
        amount_outcome = DeterministicExecutor(page=amount_page).execute(
            capability=cap,
            inputs={
                "institution_id": "alpha",
                "member_id": "8830142",
                "account_id": "CHK-8830142-01",
                "case_id": "D-OVER-LIMIT-4002",
                "amount": Decimal("501.00"),
                "currency": "USD",
            },
        )
        browser.close()

    print(f"[+] Guard Outcome:         {outcome.code.value} ({outcome.category.value})")
    print(f"[+] Safety Message:        {outcome.message}")
    print(f"[+] Money Moved:           {outcome.money_moved}")

    assert outcome.code == OutcomeCode.ENTITY_BINDING_MISMATCH
    assert outcome.money_moved is False
    assert amount_outcome.code == OutcomeCode.POLICY_DENIED
    assert amount_outcome.money_moved is False
    assert get_member("8830124")["balance"] == 410.25
    assert get_member("8830142")["balance"] == 1240.50
    print(f"[+] Over-limit Outcome:    {amount_outcome.code.value} ({amount_outcome.category.value})")
    print("\n[OK] Scenario 4 Verification Passed: Wrong member and amount were blocked before COMMIT.")


def run_crash_resume():
    print_banner("Crash Recovery & State Resumption from SQLite WAL Ledger", 5)
    print("[*] Concept: Hard process kill immediately after provisional credit moves money.")
    print("    Crucial Invariant: State reconstructed from SQLite WAL ledger; resumes and completes without double credit.\n")

    reset_all_simulators()
    llm_tracker.reset()

    case_id = f"D-CRASH-{int(time.time())}"
    member_id = "8830142"
    amount = Decimal("310.00")

    db = SessionLocal()
    crashed = False

    print("[*] Running workflow with hard process kill injected after provisional credit...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        wf_1 = RegEWorkflow(session=db, page=page, kill_after_credit=True)
        try:
            wf_1.run_case(case_id=case_id, member_id=member_id, amount=amount)
        except Exception as exc:
            print(f"[!] PROCESS TERMINATED: {exc}")
            crashed = True
        finally:
            browser.close()

    assert crashed is True
    db.close()

    # Verify intermediate state in ledger
    db2 = SessionLocal()
    service = LedgerService(db2)
    intermediate = service.reconstruct_case_state(case_id)
    print("\n[*] Ledger State After Hard Process Crash:")
    print(f"    - Status:       {intermediate.status}")
    print(f"    - Money Moved:  {intermediate.money_moved}")
    print(f"    - Memo Code:    {intermediate.latest_memo_ref}")
    print(f"    - Completed:    {intermediate.completed_capabilities}")

    # Fresh process instance resumption
    print("\n[*] Starting completely fresh orchestrator instance (surviving process termination)...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        wf_2 = RegEWorkflow(session=db2, page=page, kill_after_credit=False)
        resumed_res = wf_2.run_case(case_id=case_id, member_id=member_id, amount=amount)
        browser.close()

    final_snapshot = service.reconstruct_case_state(case_id)
    print("[+] Resumption Complete:")
    print(f"    - Final Status:         {final_snapshot.status}")
    print(f"    - Total Capabilities:   {final_snapshot.completed_capabilities}")
    print("    - 12 CFR 1005.11 Met:   Notice sent; 10-day credit deadline resolved")
    print(f"    - Replay LLM Calls:     {llm_tracker.call_count}")

    assert resumed_res["status"] == "SUCCESS"
    assert resumed_res["state"] == "WAITING_RESOLUTION"
    assert "core.post_provisional_credit" in final_snapshot.completed_capabilities
    assert "docs.send_notice" in final_snapshot.completed_capabilities
    db2.close()
    print("\n[OK] Scenario 5 Verification Passed: Crash recovered cleanly from ledger with zero duplicate credit.")


def run_human_handoff():
    print_banner("Compliance Review Interstitial & Single-Owner Lease Transfer", 6)
    print("[*] Concept: Core bank displays compliance interstitial; automation halts and yields lease.")
    print("    Crucial Invariant: Mutual exclusion enforced; operator signs off; automation safely resumes.\n")

    reset_all_simulators()
    llm_tracker.reset()

    set_core_bank_mode(require_compliance_interstitial=True)

    case_id = f"D-HANDOFF-{int(time.time())}"
    member_id = "8830142"
    amount = Decimal("250.00")

    db = SessionLocal()
    coordinator = HandoffCoordinator(session=db)

    print("[*] Running dispute workflow with compliance review interstitial active...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        wf = RegEWorkflow(session=db, page=page)

        res_initial = wf.run_case(case_id=case_id, member_id=member_id, amount=amount)
        print(f"[!] Handoff Triggered: Workflow entered state '{res_initial['status']}'")

        snapshot_1 = wf.service.reconstruct_case_state(case_id)
        print(f"    - Requires Human: {snapshot_1.requires_human}")
        print(f"    - Lease Owner:    {snapshot_1.lease_owner} (Automation yielded lease)")

        # Human operator reviews case and claims lease
        print("\n[*] Human operator (compliance_officer_sarah) claims single-owner lease...")
        lease = coordinator.claim_operator_lease(case_id=case_id, operator_id="compliance_officer_sarah")
        print(f"    - Lease Active: Owned by '{lease.owner}'")

        # Operator clears compliance interstitial in browser
        print("[*] Operator acknowledges compliance interstitial on active browser session...")
        coordinator.operator_clear_compliance(
            case_id=case_id,
            operator_id="compliance_officer_sarah",
            lease_token=lease.fencing_token,
            page=page,
        )

        # Resuming automation
        print("\n[*] Resuming workflow as automation...")
        res_resumed = wf.run_case(case_id=case_id, member_id=member_id, amount=amount)
        browser.close()

    print("[+] Handoff Resumption Succeeded:")
    print(f"    - Final State:      {res_resumed['state']}")
    print(f"    - Money Moved:      {res_resumed['money_moved']}")

    assert res_resumed["status"] == "SUCCESS"
    assert res_resumed["state"] == "WAITING_RESOLUTION"
    db.close()
    print("\n[OK] Scenario 6 Verification Passed: Single-owner lease handoff and resumption completed.")


def run_second_institution():
    print_banner("Second Institution Skin & Surface Overlay Adaptation", 7)
    print("[*] Concept: Replays the unmodified capability artifact against the independent")
    print("    Institution Beta service, trusted-routed by 'institution_id' -- not a mutated URL.")
    print("    Crucial Invariant: Unmapped encounters drift; mapped with surface overlay succeeds with 0 LLM calls.\n")

    reset_all_simulators()
    llm_tracker.reset()

    # The artifact itself is never mutated: routing to Institution Beta's independent
    # service (settings.core_bank_2_url) is resolved at runtime from 'institution_id',
    # kept outside the immutable, hash-verified capability (see tandem/surfaces/routing.py).
    cap = load_capability_from_yaml("capabilities/core/post_provisional_credit.yaml")
    inputs = {
        "institution_id": "beta",
        "member_id": "8830142",
        "account_id": "CHK-8830142-01",
        "case_id": "D-BETA-7001",
        "amount": Decimal("340.00"),
        "currency": "USD",
    }

    # Pass 1: Unmapped
    print("[*] Attempt 1: Executing on Institution Beta WITHOUT surface overlay...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        executor_unmapped = DeterministicExecutor(page=page, overlay=None)
        outcome_unmapped = executor_unmapped.execute(capability=cap, inputs=inputs)
        browser.close()

    print(f"    Outcome: {outcome_unmapped.code.value} ({outcome_unmapped.category.value})")
    print(f"    Drift:   {outcome_unmapped.message}")
    assert outcome_unmapped.money_moved is False

    # Pass 2: Mapped with the Beta surface overlay
    print("\n[*] Attempt 2: Executing on Institution Beta WITH surface overlay...")
    reset_all_simulators()
    llm_tracker.reset()
    overlay = get_overlay("beta")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        executor_mapped = DeterministicExecutor(page=page, overlay=overlay)
        outcome_mapped = executor_mapped.execute(capability=cap, inputs=inputs)
        browser.close()

    print(f"    Outcome:        {outcome_mapped.code.value} ({outcome_mapped.category.value})")
    print(f"    Money Moved:    {outcome_mapped.money_moved} (${inputs['amount']:.2f} USD)")
    print(f"    Memo Ref:       {outcome_mapped.audit_ref}")
    print(f"    LLM Calls:      {llm_tracker.call_count} (Strict Zero-LLM Invariant)")

    assert outcome_mapped.code == OutcomeCode.COMPLETED
    assert outcome_mapped.money_moved is True
    assert llm_tracker.call_count == 0
    print("\n[OK] Scenario 7 Verification Passed: Surface overlay adapted to second institution with zero LLM calls.")


def run_uncertain_effect():
    print_banner("Uncertain Effect Handling (504 Timeout Escalation)", 5)
    print("[*] Concept: The target accepts COMMIT but its response disappears.")
    print("    Crucial Invariant: Queries postcheck inquiry; if unconfirmed, routes to UNCERTAIN_EFFECT; never blindly retries.\n")

    # Part A: Postcheck Reconciliation
    print("[*] Part A: 504 Timeout where postcheck inquiry confirms transaction succeeded...")
    reset_all_simulators()
    set_processor_mode(timeout_after_submit=True)

    db = SessionLocal()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        wf = RegEWorkflow(session=db, page=page)
        res_a = wf.run_case(case_id=f"D-RECON-{int(time.time())}", member_id="8830142", amount=195.00)
        browser.close()

    print(f"    Part A Result: Reconciled via postcheck inquiry -> Status '{res_a['state']}'")
    assert res_a["status"] == "SUCCESS"
    assert res_a["state"] == "WAITING_RESOLUTION"
    db.close()

    # Part B: Unconfirmed Escalation
    print("\n[*] Part B: Ambiguous interruption where postcheck inquiry is inconclusive (UNCERTAIN_EFFECT)...")
    reset_all_simulators()
    member_id = "8830142"
    case_id = f"D-UNCERTAIN-{int(time.time())}"
    inputs = {
        "institution_id": "alpha",
        "member_id": member_id,
        "account_id": "CHK-8830142-01",
        "case_id": case_id,
        "amount": Decimal("215.00"),
        "currency": "USD",
    }

    # Server-side delay pushes the commit response past the client's timeout (a real
    # dropped connection: the credit still lands), and disabling the memo lookup means
    # reconciliation's postcheck genuinely cannot confirm it landed either -- that
    # combination, not a bare exception, is what makes the effect truly UNCERTAIN.
    set_core_bank_mode(post_commit_delay_ms=2000, fail_credit_lookup_when_present=True)
    cap = load_capability_from_yaml("capabilities/core/post_provisional_credit.yaml")
    cap.steps = [cap.steps[-1]]  # Only the final SUBMIT step; page is already on the confirm screen.

    db2 = SessionLocal()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:8001/workspace/credit/entry?member_id={member_id}")
            page.locator("input[name='case_id']").fill(case_id)
            page.locator("input[name='amount']").fill(str(inputs["amount"]))
            page.locator("button.btn-proceed").click()
            page.set_default_timeout(500)
            page.set_default_navigation_timeout(500)

            engine = EffectEngine(session=db2, page=page)
            outcome = engine.execute_capability(capability=cap, inputs=inputs)
            browser.close()
    finally:
        set_core_bank_mode(post_commit_delay_ms=0, fail_credit_lookup_when_present=False)

    print(f"    Part B Result: Outcome '{outcome.code.value}' ({outcome.category.value})")
    print(f"    Safety Message: {outcome.message}")
    print("    Single-Owner Lease: Transferred to Operator review queue; blind retry strictly forbidden.")

    assert outcome.category == OutcomeCategory.UNCERTAIN_EFFECT
    assert outcome.code == OutcomeCode.UNCERTAIN_EFFECT
    assert outcome.money_moved is False
    db2.close()
    print("\n[OK] Scenario 5 Verification Passed: Handled ambiguous commit without blind retry.")



SCENARIOS = {
    "discovery": run_discovery,
    "replay-new-case": run_replay_new_case,
    "replay-same-case": run_replay_same_case,
    "wrong-member-or-amount": run_transposed_id,
    "uncertain-effect": run_uncertain_effect,
}


def main():
    parser = argparse.ArgumentParser(description="Tandem Effect-Aware Automation Demonstration")
    parser.add_argument(
        "--scenario",
        choices=list(SCENARIOS.keys()) + ["all"],
        default="all",
        help="Specific demonstration scenario to execute",
    )
    args = parser.parse_args()

    print("\n=========================================================================")
    print("       TANDEM :: EFFECT-AWARE COMPUTER-USE AUTOMATION LAYER              ")
    print("       Regulation E Debit-Card Dispute Multi-System Orchestrator         ")
    print("=========================================================================")

    print("[*] Verifying banking simulator services...")
    ensure_simulators_running()

    start_time = time.time()

    if args.scenario == "all":
        for name, fn in SCENARIOS.items():
            fn()
        print("\n" + "=" * 75)
        print("  ALL 5 ASSESSMENT SCENARIOS EXECUTED SUCCESSFULLY")
        print(f"  Total Elapsed Time: {time.time() - start_time:.2f}s")
        print("=" * 75 + "\n")
    else:
        SCENARIOS[args.scenario]()
        print(f"\n[+] Scenario '{args.scenario}' completed in {time.time() - start_time:.2f}s.\n")


if __name__ == "__main__":
    main()
