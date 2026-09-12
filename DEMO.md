# Tandem Demonstration & Verification Guide

This guide provides step-by-step instructions for reproducing, observing, and verifying all **8 core demonstration scenarios** of Tandem, an effect-aware computer-use automation layer for legacy financial systems.

---

## Prerequisites & Quick Start

Ensure your environment is initialized using Python 3.12 and `uv`:

```bash
# 1. Sync dependencies and install Playwright Chromium
uv sync
uv run playwright install chromium

# 2. Seed initial demo test cases in SQLite WAL Ledger
uv run python scripts/seed.py
```

### Running the Services

You can run the full multi-service stack (Operator API on 8000, Core Banking on 8001, Dispute Processor on 8003, Document Notice Generator on 8004) either via Python script or Docker:

```bash
# Option A: Local multi-process runner
uv run python scripts/start_services.py

# Option B: Docker Compose
docker compose up --build
```

> **Note**: The demo script (`scripts/demo.py`) automatically ensures test simulator servers are running in the background if they are not already started.

---

## Running the Demonstrations

The CLI demo runner supports executing each scenario individually or all 8 in sequence:

```bash
# Run all 8 scenarios sequentially
uv run python scripts/demo.py --scenario all

# Run individual scenarios by name
uv run python scripts/demo.py --scenario discovery
uv run python scripts/demo.py --scenario replay-new-case
uv run python scripts/demo.py --scenario replay-same-case
uv run python scripts/demo.py --scenario transposed-id
uv run python scripts/demo.py --scenario crash-resume
uv run python scripts/demo.py --scenario human-handoff
uv run python scripts/demo.py --scenario second-institution
uv run python scripts/demo.py --scenario uncertain-effect
```

---

## Scenario Deep Dives

### Scenario 1: Discovery & Capability Compilation
**Goal**: Demonstrate that an LLM agent is used *only* during capability discovery to explore hostile legacy banking interfaces and compile deterministic, versioned, cryptographic YAML capability artifacts.

```bash
uv run python scripts/demo.py --scenario discovery
```

**What Happens Under the Hood**:
1. Playwright launches a browser session against `http://localhost:8001/` (Core Bank Simulator).
2. `DiscoveryAgent` navigates the legacy frameset, table search, and account details.
3. It discovers the target fields (Member Search, Transaction Table, Credit Memo Modal).
4. `CapabilityCompiler` writes `capabilities/compiled/demo_post_provisional_credit.yaml` containing:
   - Typed effect definition (`COMMIT`, idempotency precheck query, postcheck assertion).
   - Scoped container selector (`#account-detail-container`).
   - Execution steps (Playwright locators, actions, parameters).
   - Cryptographic SHA-256 artifact hash.
5. Invariant verified: Discovery completes with recorded LLM telemetry; compiled artifact is ready for zero-LLM replay.

---

### Scenario 2: Zero-LLM Deterministic Replay on New Case
**Goal**: Prove the core production invariant: **Replay executes with exactly 0 LLM calls**.

```bash
uv run python scripts/demo.py --scenario replay-new-case
```

**What Happens Under the Hood**:
1. Workflow loads fresh dispute case `D-DEMO-002` ($250.00) for member `8830142`.
2. `DeterministicExecutor` runs the compiled capability artifact.
3. It performs container verification, fills credit amount, selects memo type, and commits.
4. Transaction is posted with memo code `MC-7621`.
5. Event `POST_PROVISIONAL_CREDIT` is appended to the SQLite WAL procedure ledger.
6. The test explicitly asserts:
   ```python
   assert llm_tracker.call_count == 0
   ```

---

### Scenario 3: Precheck Idempotency (Prevent Duplicate Credit)
**Goal**: Solve the catastrophic flaw of standard browser agents: **"The procedure didn't finish, replay blindly posted credit twice."**

```bash
uv run python scripts/demo.py --scenario replay-same-case
```

**What Happens Under the Hood**:
1. The demo executes the provisional credit capability against case `D-DEMO-002`, which was already credited in Scenario 2.
2. Before clicking the submit button or entering data, `EffectEngine` executes the declared precheck:
   - Query: `check_provisional_credit_exists(member_id="8830142", case_id="D-DEMO-002")`
3. The simulator reports credit memo `MC-7621` already exists on the ledger.
4. Engine halts immediately with outcome:
   - `OutcomeCategory.SKIPPED_IDEMPOTENT`
   - `OutcomeCode.ALREADY_APPLIED`
5. Verified Result: **Zero duplicate transactions created. $0.00 excess funds moved.**

---

### Scenario 4: Scoped Container Guard (Transposed Member ID)
**Goal**: Demonstrate spatial and structural identity binding to prevent crediting the wrong account in legacy interfaces with ambiguous search results.

```bash
uv run python scripts/demo.py --scenario transposed-id
```

**What Happens Under the Hood**:
1. A dispute case specifies member ID `8830142`.
2. A typo or hostile fuzzy search returns two confusable accounts: `8830142` (Jane Doe) and `8830124` (John Smith).
3. The browser locator clicks the first row (`8830124`).
4. Before issuing any keyboard inputs or clicking commit, `ScopedContainerGuard` inspects the active bounding container:
   - Expected Member ID: `8830142`
   - Container Text: Found `8830124`
5. Guard raises `EntityBindingMismatchError` and halts with:
   - `OutcomeCategory.FATAL_GUARD`
   - `OutcomeCode.ENTITY_BINDING_MISMATCH`
6. Verified Result: Automation halts before posting credit to the wrong human.

---

### Scenario 5: Mid-Procedure Crash & Deterministic Resumption
**Goal**: Prove fault-tolerant procedure state recovery from an append-only WAL ledger across hard process termination.

```bash
uv run python scripts/demo.py --scenario crash-resume
```

**What Happens Under the Hood**:
1. Step 1: Dispute `D-DEMO-005` initiates workflow.
2. Injected Fault: `crash_after_effect=True` triggers an immediate unhandled exception after provisional credit is posted to the core bank.
3. The process state, memory, and browser context are completely destroyed.
4. Step 2: Resumption process starts with a fresh `RegEWorkflow` instance reading from the persisted SQLite database.
5. `RegEWorkflow.reconstruct_state()` replays the ledger events:
   - Found `CREDIT_POSTED` with memo code `MC-7621`.
6. State machine advances to `DISPUTE_SUBMITTED_TO_NETWORK`.
7. Execution resumes without re-running credit posting, seamlessly finishing card network filing and notice dispatch.
8. Verified Result: Final state is `NOTICE_DISPATCHED`. Exactly **one** credit posted in core banking.

---

### Scenario 6: Human Handoff with Single-Owner Lease
**Goal**: Safely handle compliance interstitials (e.g., identity verification popups, fraud alerts) by transferring control to a human without race conditions.

```bash
uv run python scripts/demo.py --scenario human-handoff
```

**What Happens Under the Hood**:
1. Core bank simulator displays a blocking compliance modal: *"Customer signature verification required"*.
2. `EffectEngine` identifies interstitial and halts automation.
3. `HandoffCoordinator` writes a `HANDOFF_REQUESTED` event to the ledger and transfers case lease ownership:
   - Owner: `HUMAN`
   - Automation Execution: Blocked (raises `LeaseConflictError` if automation attempts execution).
4. Operator visits console or API, performs manual verification, and acknowledges handoff via `/api/cases/{case_id}/handoff/clear`.
5. Lease ownership is returned to `AUTOMATION`.
6. Workflow resumes and finishes the dispute lifecycle.

---

### Scenario 7: Multi-Institution Adaptation & Drift Detection
**Goal**: Demonstrate that cross-institutional UI variations (different DOM structures, button text, table layouts) are handled via Surface Overlays without changing core workflow logic or requiring LLMs.

```bash
uv run python scripts/demo.py --scenario second-institution
```

**What Happens Under the Hood**:
1. Step 1 (Drift Failure): Run the unmodified capability artifact against Institution Beta's independent service (port 8002), trusted-routed at runtime from `institution_id` (never a mutated artifact URL). Beta uses `<div role="form">` and different CSS classes instead of legacy tables.
2. Selector fails with `SurfaceDriftError` (`OutcomeCode.ELEMENT_NOT_FOUND`).
3. Step 2 (Overlay Success): Provide the Beta surface overlay that maps semantic targets to Beta's DOM elements.
4. Replay executes successfully with **0 LLM calls**.

---

### Scenario 8: Uncertain-Effect Timeout Reconciliation
**Goal**: Solve the distributed systems failure where an external service times out (HTTP 504) after an HTTP request was sent, leaving its execution state unknown.

```bash
uv run python scripts/demo.py --scenario uncertain-effect
```

**What Happens Under the Hood**:
1. The workflow issues a document generation call to the Notice Simulator with `force_timeout=True`.
2. Notice service times out (HTTP 504 / Gateway Timeout).
3. Tandem refuses to blindly retry (which would risk sending duplicate compliance letters or incurring double fees).
4. `ReconciliationProtocol` runs a postcheck query to ascertain whether the document was generated.
5. If state cannot be reconciled, the engine transitions the case to `UNCERTAIN_EFFECT` and halts.
6. A critical human escalation alert is raised in the Operator Dashboard.

---

## Verifying in the Operator Console

Open your browser to:
[http://localhost:8000/api/dashboard](http://localhost:8000/api/dashboard)

In this UI you can observe:
- **Active Case Ledger**: Status, account ID, member ID, provisional credit amount, and memo reference.
- **Audit Event Timeline**: Chronological, cryptographically verifiable log of every `PROBE`, `INTENT`, `PRECHECK`, `COMMIT`, `POSTCHECK`, and `TRANSITION` event.
- **Statutory 10-Day Clock Monitor**: Tracks compliance time remaining under 12 CFR § 1005.11(c).
- **Lease Controls**: View whether `AUTOMATION` or `HUMAN` holds exclusive write permissions for each dispute.
