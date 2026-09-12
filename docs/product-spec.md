# Tandem Product Specification

*Derived directly from `Tandem-Problem-and-Solution.pdf` (interface.ai Take-Home Assignment A - Problem & Solution) with explicit engineering boundaries.*

---

## 1. Problem Statement

> **"The money moved. The procedure didn't finish."**

At approximately one hundred US community banks and credit unions, AI agents (voice, chat) capture member disputes. However, finishing the job requires operating four legacy vendor consoles (core banking platforms like Symitar / DNA / Keystone, card processor portals like CO-OP / PSCU / Visa DPS, case trackers, document distribution systems) that expose no APIs to one another.

A human operator currently acts as the integration layer between these disconnected consoles.

When browser-based computer-use automation is applied to this cross-system procedure, standard record-and-replay systems verify that **a control was actuated and a page was reached**. But when actions mutate state or move real money, naive step replay causes catastrophic failures:
1. **Double application:** A step fails partway through a procedure. The retry starts again from the top. Provisional credit is posted a second time, paying the member twice.
2. **Wrong entity:** A locator resolves correctly on the wrong row/record. In fuzzy search results with non-deterministic row ordering, member `8830142` and `8830124` look almost identical. A weak page-level assertion like *"member ID appears somewhere on this page"* passes for both, and money is posted to the wrong member.
3. **Orphaned procedure:** Provisional credit posts in core banking, but the chargeback filing times out at the processor portal. The mandatory 2-business-day member notice is never sent. The process dies between two systems with no durable ledger recording its state, violating federal regulations.

---

## 2. Target User & Regulatory Context

### Target User
- **Back-Office Operations & Compliance Officers** at community banks and credit unions.
- **Human Exception Handlers:** Operators who take over live sessions when automated procedures encounter compliance interstitials, policy blocks, or ambiguous states.

### Regulatory Mandate: Regulation E (12 CFR 1005.11)
- When a member reports an unauthorized electronic fund transfer, the institution must investigate and resolve within **10 business days**, or post **provisional credit** within those 10 business days and conclude investigation within **45 calendar days** (extendable to 90 days for certain point-of-sale or foreign transactions).
- **Mandatory Notice Deadline:** The institution must notify the member of provisional credit within **2 business days** of crediting.
- **Zero-Tolerance Enforcement:** Missing the deadline is itself a federal regulatory violation, independent of whether the transaction was unauthorized. The clock does not pause for crashes or timeouts.

---

## 3. Workflow Scope: Regulation E Debit-Card Dispute

The target end-to-end workflow spans four distinct systems:

| Step | System | Type | Consequence |
|---|---|---|---|
| 1. Verify member & locate transaction | Core banking (Symitar / DNA / Keystone) | `READ` | Idempotent / Safe |
| 2. Check prior or duplicate dispute | Core banking + Case tracker | `READ` | Prevents duplicate processing |
| 3. File chargeback | Card processor portal (CO-OP / PSCU / Visa DPS) | `COMMIT` | External business mutation |
| 4. Post provisional credit | Core banking | `COMMIT` | Moves real money (irreversible effect) |
| 5. Send required member notice | Document / Notice delivery system | `COMMIT` | Regulated 2-day business deadline |
| 6. Finalize or reverse + notice | Core + Document system | `COMMIT` | Dispute resolution |

---

## 4. Proposed Architecture & Core Moves

Tandem is an **effect-aware computer-use automation layer**.
A capability is **not** a recorded flow; it is a recorded flow plus a **declared effect on the world**. The replay engine guarantees that the declared effect happens safely and idempotently.

```
       Member / Case Intake (D-8842, $340.00)
                         │
                         ▼
        ┌──────────────────────────────────┐
        │     Procedure State Machine      │◄── Durable Ledger (SQLite)
        │   (Reg E Dispute Orchestration)  │    (Reconstructs state across crash)
        └────────────────┬─────────────────┘
                         │
                         ▼
        ┌──────────────────────────────────┐
        │      Policy & Guard Engine       │─── Bounds, Currencies, Scoped Guards
        └────────────────┬─────────────────┘
                         │
                         ▼
        ┌──────────────────────────────────┐
        │    Effect-Aware Commit Engine    │
        │  Precheck ──► Guard ──► Commit   │
        │    └──► Postcheck ──► Reconcile  │
        └────────────────┬─────────────────┘
                         │
                         ▼
        ┌──────────────────────────────────┐
        │     Surface Abstraction Layer    │─── Base locators + Overlays + Drift
        └────────────────┬─────────────────┘
                         │
                         ▼
           Deterministic Playwright Replay
          (Zero LLM Calls in Replay Path)
                         │
        ┌────────────────┴─────────────────┐
        ▼                                  ▼
Core Banking Simulator            Card Processor Simulator
(Hostile legacy UI)               (Failure switches & timeouts)
```

### Four Structural Pillars (From Spec)

1. **Move 1 — Effect-Typed Capabilities with Precheck & Postcheck:**
   - `READ`: No external mutation.
   - `STAGE`: Reversible mutation.
   - `COMMIT`: Irreversible mutation, money movement, or regulatory clock start.
   - Every `COMMIT` **must** declare:
     - `idempotency_key`
     - `precheck`: e.g. querying memos/ledger for existing case reference
     - `postcheck`: verifying mutation completed
     - `reconciliation`: rules for resolving ambiguous execution
     - `bounds`: amount limits (e.g. max $500.00, USD)
     - `compensation`: reversal capability if available

2. **Move 2 — Durable Procedure Ledger:**
   - Append-only event store in SQLite.
   - Persists every step, status, timestamps, money moved (`MONEY_MOVED=true`), expected/observed entities, and deadlines.
   - Reconstructs case state after hard process termination.
   - Business-day deadline calculation (accounting for weekends and cutoff times).

3. **Move 3 — Control-Scoped Identity & Amount Guards:**
   - Rejects whole-page text assertions.
   - Directly inspects the **parent container / logical row** enclosing the action control (button).
   - Reads account number and amount from the same container immediately prior to clicking commit.
   - Detects transposed digits (`8830142` vs `8830124`) and halts with `HARD_FAILURE / ENTITY_BINDING_MISMATCH`.

4. **Move 4 — Escalation Carries the Clock, Not Just the Screen:**
   - When automation pauses (e.g. compliance interstitial or uncertain state), control transfers via a single-owner lease (`AUTOMATION` vs `HUMAN`).
   - Human operator drives the **same live browser session** (not a fresh one), reviews case context and deadline, completes or clears the blocker, and returns control.
   - Every human action is recorded in the immutable audit ledger.

---

## 5. What Must Be Built, Stubbed, and Designed

| Status | Component | Scope in Tandem |
|---|---|---|
| **Built for real** | Hostile Core Banking Simulator | Framesets/iframes, tables, no test IDs, unstable IDs, duplicate text, fuzzy search, transposed accounts, session expiry, latency. |
| **Built for real** | Second Institution Simulator | Different branding, layout, DOM hierarchy to prove Surface Overlays and drift detection. |
| **Built for real** | Surface Abstraction & Overlays | Decoupled semantic locators from brittle CSS/XPath; handles drift without LLM in replay. |
| **Built for real** | LLM Discovery & Capability Compiler | Model-driven exploration recording interactions into typed, versioned YAML/JSON artifacts. |
| **Built for real** | Deterministic Replay Engine | Playwright-based replay strictly executing compiled artifacts with **0 LLM calls**. |
| **Built for real** | Procedure Ledger & Crash Recovery | SQLite append-only event store, process crash injection (`PROCESS_KILL_AFTER`), state reconstruction. |
| **Built for real** | Policy & Scoped Guards | Input validation, amount bounds, container-scoped identity checks. |
| **Built for real** | Live-Session Human Handoff | Single-owner lease, interstitial detection, operator takeover, resumption. |
| **Built for real** | Postcheck & Reconciliation | Scenario 8: handling timeouts without blind retry; classifying into `UNCERTAIN_EFFECT`. |
| **Deliberately thin** | Card Processor Portal | Single screen with failure switches (`SESSION_EXPIRED`, dropped connection timeout) to test ledger resilience. |
| **Stubbed at clean seam**| Document/Notice System | Records notice creation, timestamp, and deadline tracking; bare HTML interface. |
| **Stubbed at clean seam**| Operator Console UI | Minimal HTML/Jinja interface displaying case timeline, live session link, handoff actions. |
| **Designed, not built** | Capability Registry, Credential Vault, Desktop OS automation, Distributed queues | Clean abstraction boundaries; not implemented for take-home scope. |

---

## 6. Demonstration Scenarios

1. **`discovery`**: LLM agent discovers how to post provisional credit on the hostile simulator and compiles a versioned capability artifact.
2. **`replay-new-case`**: Deterministic replay with **0 LLM calls**, executes against new case, posts credit once, returns reference.
3. **`replay-same-case`**: Precheck detects existing memo/case reference, returns `ALREADY_APPLIED`, touches nothing, prevents double credit.
4. **`transposed-id`**: Container guard detects transposed member ID (`8830124` vs `8830142`), halts with `ENTITY_BINDING_MISMATCH`, prevents misdirected funds.
5. **`crash-resume`**: Mid-procedure process kill after provisional credit; restart reconstructs state from ledger, skips double credit, highlights pending notice & deadline.
6. **`human-handoff`**: Automation encounters compliance review interstitial, yields single-owner lease to human operator, operator clears step, automation completes.
7. **`second-institution`**: Replays capability on a re-skinned, structurally modified banking simulator using a surface overlay, detecting drift.
8. **`uncertain-effect`**: Network drop after commit submit; postcheck/reconciliation executes; if reference unconfirmed, transitions to `UNCERTAIN_EFFECT` and routes to human escalation instead of blind retry.

---

## 7. Known Weaknesses & Technical Defense

- **Enterprise Dispute Software (Quavo, Pegasystems Smart Dispute, Visa DPS APIs):**
  - *Counter-argument:* Enterprise platforms are prohibitively expensive ($500k+ annual commitments) and require massive multi-year IT overhauls. Community institutions cannot afford them or obtain custom API integrations from their core providers. Tandem automates the existing human console tier at a fraction of the cost.
  - *Fallback domain:* Fee reversals, wire verification, address changes—all live on teller consoles with zero API prospect.
- **Exactly-Once Claims Against Arbitrary Legacy Systems:**
  - *Correction:* True mathematical exactly-once execution against arbitrary third-party black-box systems is impossible across network partitions. Tandem claims **effect-aware replay, idempotency enforcement where references exist, durable recovery, postcheck reconciliation, and escalation to human oversight for uncertain effects**.
