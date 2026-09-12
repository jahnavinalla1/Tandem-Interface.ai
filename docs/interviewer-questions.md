# Tandem: Technical Interviewer & Architectural Review Guide

This guide compiles anticipated questions, design trade-offs, and architectural justifications for technical interviewers, system architects, and engineering leaders evaluating the Tandem platform.

---

## 1. Foundational Architecture & Philosophy

### Q1: Why not simply use an autonomous LLM agent (e.g., Browser-Use, Claude Computer Use, LangGraph) for production execution?
**Answer**:
Autonomous LLM agents are non-deterministic, high-latency, and prone to hallucinations. In financial systems, the central risk is:
> *"The money moved. The procedure didn't finish."*

If an autonomous LLM agent crashes or receives a 504 timeout after posting provisional credit, its next step is stochastic. It might:
1. Re-run the entire prompt and click "Post Credit" again, issuing duplicate credit.
2. Select a confusable customer from a fuzzy search result.
3. Hallucinate that the transaction succeeded when it failed, leaving the procedure orphaned and violating federal statutory deadlines.
4. Incur massive API costs ($0.05–$0.20 per browser step) and 3–10 second latency per action.

**Tandem's Solution**:
Tandem establishes a **Zero-LLM Replay Invariant**. The LLM is strictly quarantined to **Discovery**—exploring hostile, unfamiliar interfaces and compiling typed, versioned, cryptographic YAML capability artifacts. In production, **replay is 100% deterministic**, executing raw Playwright commands at sub-100ms speeds with **0 LLM calls**, backed by a formal effect protocol.

---

### Q2: Why not just write standard RPA (UiPath) or raw Playwright scripts?
**Answer**:
Standard RPA scripts and raw Playwright test scripts suffer from two fatal weaknesses:
1. **Lack of Effect Semantics**: A raw Playwright script does not distinguish between a benign click (`PROBE`, e.g., expanding an accordion or navigating a menu) and an irreversible state-mutating transaction (`COMMIT`, e.g., posting general ledger credit or filing a chargeback). If a step fails, RPA either crashes completely or retries blindly.
2. **Selector Fragility & Zero Drift Awareness**: Traditional scripts hardcode CSS/XPath selectors. When an institution updates its styling or a second bank branch uses a different UI skin, scripts fail with opaque timeout errors. Tandem's **Surface Overlays** decouple semantic targets from DOM implementations, and its drift detection provides actionable diagnostic failures.

---

## 2. Distributed State, Concurrency & Idempotency

### Q3: Walk me through the lifecycle of a `COMMIT` capability. How is double-credit prevented?
**Answer**:
Tandem executes every irreversible capability through a strict **14-step Effect Protocol**:
1. **Intent Logging**: Appends a `PENDING_COMMIT` event with a unique procedure run ID to the SQLite WAL ledger.
2. **Policy Bounds Check**: Validates that transaction amounts, currencies, and memo types comply with domain rules (e.g., amount between \$0.01 and \$5,000.00).
3. **Idempotency Precheck**: Before interacting with the DOM, queries the simulator/backend (`check_provisional_credit_exists`). If a credit matching the member and case already exists, execution halts with `SKIPPED_IDEMPOTENT` (`ALREADY_APPLIED`).
4. **Scoped Container Verification**: Locates the boundary container (e.g., `#account-detail-container`) and verifies that the displayed member ID exactly matches the case payload, preventing misdirected funds on confusable/transposed accounts.
5. **DOM Action**: Executes the Playwright keyboard/mouse interactions.
6. **Postcheck Verification**: Immediately inspects the updated DOM and transaction table to confirm receipt of the memo code.
7. **Ledger Commit**: Appends `CREDIT_POSTED` with memo reference to the append-only ledger.

---

### Q4: How does Tandem handle network drops or HTTP 504 timeouts on write operations?
**Answer**:
When an external banking service or document generator drops the connection or times out, standard software retries the request. In financial operations, this is dangerous because the remote server may have processed the credit before the connection died.

Tandem enters the **Reconciliation Protocol**:
1. Categorizes the event as `UNCERTAIN_EFFECT`.
2. Prohibits automatic retry.
3. Executes a specialized out-of-band reconciliation probe (`query_transaction_by_reference`).
4. If the probe confirms the effect occurred, it updates the ledger and advances state.
5. If the probe cannot verify state, it raises a fatal `UNCERTAIN_EFFECT` alert, transitions case ownership to `HUMAN`, and triggers an operator notification.

---

### Q5: Why did you choose an SQLite WAL ledger over Postgres or Redis?
**Answer**:
1. **Zero-Dependency Crash Resilience**: A browser automation agent frequently runs in containerized workers or edge nodes. External network failures should not disrupt the agent's ability to atomically record its own intent before issuing a browser click.
2. **Write-Ahead Logging (WAL)**: SQLite in WAL mode allows concurrent readers (e.g., the Operator API and Dashboard) while a worker process is appending events, with `busy_timeout=30000` handling contention cleanly.
3. **Repository Pattern Portability**: The ledger is abstracted through `LedgerRepository` and SQLAlchemy ORM models (`ProcedureCaseRecord`, `ProcedureEventRecord`). In an enterprise deployment with thousands of parallel workers, swapping to a distributed PostgreSQL cluster requires changing one configuration string (`DATABASE_URL`) without modifying a single line of domain or workflow logic.

---

## 3. Regulatory & Domain Specifics

### Q6: What is Regulation E (12 CFR § 1005.11) and why is the 10-day statutory clock critical?
**Answer**:
Under Federal Reserve Regulation E (Consumer Financial Protection Bureau 12 CFR § 1005.11(c)), financial institutions must investigate consumer notices of unauthorized electronic fund transfers:
- If the institution cannot complete its investigation within **10 business days** of receiving notice, it **must provisionally credit** the consumer’s account for the disputed amount (plus interest if applicable) while continuing the investigation.
- If provisional credit is not posted within 10 business days, the institution faces statutory penalties, compliance findings, and CFPB consent orders.
- If provisional credit is posted, the bank has up to 45 (or 90) calendar days to complete the investigation.

Tandem's `StatutoryDeadlineMonitor` tracks business days (excluding weekends and federal banking holidays), warning operators when cases approach the 10-day limit and prioritizing capability execution accordingly.

---

### Q7: How does Tandem prevent human specialists and automated agents from conflicting (Split-Brain)?
**Answer**:
Tandem implements an atomic **Single-Owner Lease Model**:
- Every case in the ledger has a `lease_owner` field (`AUTOMATION` or `HUMAN`).
- When automation encounters a compliance interstitial (e.g., signature verification required, suspicious KYC alert), `HandoffCoordinator` sets `lease_owner = "HUMAN"` and logs `HANDOFF_REQUESTED`.
- If an automated worker attempts to execute steps on a case owned by `HUMAN`, the engine immediately throws `LeaseConflictError`.
- Once the specialist resolves the issue in the banking console, they clear the handoff via the Operator API (`POST /api/cases/{case_id}/handoff/clear`), atomically reassigning the lease to `AUTOMATION`.

---

## 4. Engineering Trade-offs & Production Roadmap

### Q8: What trade-offs were made in this implementation, and what would you build next?
**Answer**:
1. **Synchronous Playwright vs Asyncio Fleet**:
   - *Current*: Playwright Sync API inside `DeterministicExecutor` for predictable, linear procedural execution and simplified crash injection.
   - *Production Roadmap*: Distributed task queues (Temporal.io or Celery) dispatching headless Playwright sessions across ephemeral worker nodes with distributed tracing (OpenTelemetry).
2. **Simulated Hostile UIs vs Citrix/Canvas Terminals**:
   - *Current*: Realistic HTML framesets, tables, and modals with client-side delays and failure switches.
   - *Production Roadmap*: For green-screen 3270 terminals or Citrix virtual desktops where no DOM exists, integrate a Computer-Vision fallback surface utilizing localized OCR bounding boxes governed by the same `ScopedContainerGuard` contracts.
3. **Secrets Management**:
   - *Current*: Environment-based credential configuration.
   - *Production Roadmap*: Just-in-Time ephemeral banking credentials fetched from HashiCorp Vault with hardware-token MFA emulation.
