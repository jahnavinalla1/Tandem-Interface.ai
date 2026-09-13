# Tandem Security Architecture & Policy

This document outlines the security model, threat mitigations, policy enforcement mechanisms, and architectural invariants implemented in Tandem to ensure fail-safe computer-use automation across regulated financial environments.

---

## 1. Threat Model & Invariants

Tandem operates in environments where browser automation interacts directly with core banking general ledgers, card network gateways, and customer account records. In these environments, naive browser agents (e.g., standard LLM-based web agents) introduce catastrophic financial and compliance risks.

### Core Threat Scenarios Mitigated by Tandem:

| Threat / Failure Mode | Standard Agent Behavior | Tandem Defense Mechanism |
|---|---|---|
| **Ambiguous Account / Fuzzy Search** | Selects first row or hallucinates selector; posts funds to confusable or transposed account (`8830124` vs `8830142`). | **Scoped Container Guards**: Enforces spatial boundary and verifies exact textual identity binding within the DOM container before any keyboard/mouse commit. |
| **Crash & Replay Double-Credit** | Replaying workflow from beginning blindly re-enters credit amount, issuing duplicate provisional credits ($250 -> $500). | **Idempotency Precheck & WAL Ledger**: Validates whether transaction exists prior to execution; recovers state deterministically from append-only SQLite WAL ledger. |
| **Network Timeout on Write (504)** | Retries API call or clicks submit again, resulting in double-debit or double-credit. | **Reconciliation Protocol**: Treats timeouts as `UNCERTAIN_EFFECT`; runs postchecks and escalates to human without blind retry. |
| **Prompt Injection via DOM** | Hostile text in banking notes or memo fields manipulates LLM planner into unauthorized actions. | **Zero-LLM Replay Invariant**: LLM is strictly isolated to discovery. Production execution uses deterministic, compiled YAML capabilities with 0 LLM calls. |
| **Split-Brain Automation vs Human** | Automation and human dispute specialist simultaneously edit case, overwriting audit states. | **Single-Owner Case Leases**: Atomic lease mutex ensures only `AUTOMATION` or `HUMAN` holds execution rights at any timestamp. |

---

## 2. Zero-LLM Replay Security Boundary

A fundamental architectural boundary in Tandem is the complete isolation between **Discovery** and **Execution**:

```
+-------------------------------------------------------------------+
|                         DISCOVERY PHASE                           |
|  [Hostile UI] <---> [Discovery Agent] <---> [LLM Planner]         |
|                                |                                  |
|                                v                                  |
|                     [Capability Compiler]                         |
+--------------------------------|----------------------------------+
                                 | Produces Immutable YAML Artifact
                                 | (Signed with SHA-256 Digest)
                                 v
+-------------------------------------------------------------------+
|                         REPLAY EXECUTION                          |
|  [Deterministic Executor] <---> [Effect Engine] <---> [Core Bank] |
|              |                         |                          |
|              v                         v                          |
|     (llm_call_count == 0)      [SQLite WAL Ledger]                |
+-------------------------------------------------------------------+
```

### Security Properties:
1. **Zero Prompt Injection Surface**: Because no LLM evaluates web content or makes decisions during replay, malicious customer data (e.g., in transaction descriptions or dispute reasons) cannot alter workflow execution.
2. **Deterministic Reproducibility**: Given the same inputs and ledger state, execution produces the identical sequence of Playwright actions every time.
3. **Automated Enforcement**: All integration and E2E test suites assert `llm_tracker.call_count == 0`.

---

## 3. Scoped Container Guards (Identity Binding)

Legacy banking applications frequently feature framesets, multi-column search results, and confusable customer names or IDs. Tandem prohibits global selectors for any state-mutating capability (`COMMIT`).

### Container Guard Specification:
```yaml
scoped_guard:
  container_selector: "#account-detail-container"
  target_role: "panel"
  verify_text: "{member_id}"
  fail_action: "HALT_PROCEDURE"
```

### Enforcement Protocol:
1. Prior to interacting with any input field or button inside the target container, the `ScopedContainerGuard` locates the container element.
2. It asserts visibility and inspects `inner_text()` or attributes.
3. If the expected identifier (`member_id`, `account_number`) is absent or transposed (e.g., `8830124` when `8830142` was expected), execution halts immediately with `EntityBindingMismatchError`.
4. An `ENTITY_BINDING_MISMATCH` fatal audit event is recorded, preventing funds from moving to an unintended account.

---

## 4. Policy Bounds & Value Verification

Tandem enforces strict domain boundaries on all parameters passed to capability executions:

```python
class PolicyBounds(BaseModel):
    min_amount: Decimal = Decimal("0.01")
    max_amount: Decimal = Decimal("5000.00")
    allowed_currencies: list[str] = ["USD"]
    allowed_memo_types: list[str] = ["PROVISIONAL_CREDIT", "DISPUTE_ADJUSTMENT"]
```

- **Amount Clamping**: Prevents negative values, zero-dollar commits, or amounts exceeding statutory Regulation E dispute thresholds.
- **Enumerated Types**: Restricts transaction memo types to explicitly whitelisted categories.
- **Type Coercion Defense**: All numeric inputs are validated as `Decimal` instances to avoid floating-point rounding errors common in legacy banking adjustments.

---

## 5. Single-Owner Leases & Concurrency Control

To prevent split-brain conditions where human operators and automated agents attempt simultaneous actions on a dispute case, Tandem implements atomic single-owner leases in the SQLite ledger:

- **Lease States**: `UNASSIGNED`, `AUTOMATION`, `HUMAN`, `TERMINATED`.
- **Enforcement**:
  - Before executing any workflow step, `RegEWorkflow` verifies that `case.lease_owner == "AUTOMATION"`.
  - When a compliance interstitial or anomaly is encountered, the lease is atomically transitioned to `HUMAN`.
  - Any subsequent automated execution attempt raises `LeaseConflictError` and is rejected at the API/engine layer.
  - Humans acknowledge and clear handoffs via the secure Operator API (`POST /api/cases/{case_id}/handoff/clear`), returning the lease to `AUTOMATION` with an operator audit note.

---

## 6. Audit Trail & Cryptographic Integrity

1. **Append-Only SQLite WAL Ledger**:
   - The ledger table is strictly append-only. Events are immutable once written.
   - Uses SQLite Write-Ahead Logging (`PRAGMA journal_mode=WAL;`) with `busy_timeout=30000` to ensure crash-safe, multi-process durability.
2. **Capability Artifact Hashing**:
   - Every compiled capability YAML file includes a SHA-256 checksum (`artifact_hash`).
   - At load time, Tandem recalculates the SHA-256 hash of the artifact content. If the file has been tampered with or modified out-of-band, the loader refuses to execute the capability.
3. **Structured PII Masking**:
   - Account numbers and card primary account numbers (PANs) are truncated/masked in audit logs (e.g., `****1234`).
   - Passwords and auth tokens are excluded from DOM trace dumps and ledger events.

---

## 7. Network Exposure & Admin/Operator Authentication

Every simulator's failure-injection and reset routes (`/api/reset`, `/api/set_mode`,
`/api/set_compliance_interstitial`, `/api/set_session_valid`, `/api/set_failure`,
`/api/set_credit_lookup_failure`, `/api/set_post_commit_delay`), the actual
money-moving commit routes (core_bank `/workspace/credit/confirm`,
`/workspace/credit/commit`, `/workspace/credit/clear_compliance` on both Alpha and
Beta; processor `/chargeback/file`; documents `/notices/send`), and the Tandem
operator console's mutation routes (case lease claim/release, brokered
browser-session actions) all require a shared bearer token, and the launcher binds
every service to loopback (`127.0.0.1`) by default:

- **Bind default**: `scripts/start_services.py` (and the `tandem` console script) bind
  to `TANDEM_HOST`, which defaults to `127.0.0.1`. The containerized deployment
  (`docker-compose.yml`) explicitly sets `TANDEM_HOST=0.0.0.0` because it must be
  reachable through the container's published ports; running outside a container
  keeps every admin route off the network by default.
- **Token authentication**: every mutation route above rejects requests unless they
  carry `Authorization: Bearer <TANDEM_ADMIN_TOKEN>`. The default value
  (`tandem-local-dev-admin-token-change-me`) is for local development only --
  **any shared or production-like deployment must override `TANDEM_ADMIN_TOKEN`.**
- **CSRF defense on browser-rendered forms**: the operator console's lease
  claim/release forms, and every core_bank workspace form that leads to a commit
  route, cannot set a custom header, so they carry the same token as a hidden
  `admin_token` form field instead. A cross-origin page cannot read that token out
  of the victim's same-origin page, so it cannot forge a valid submission either --
  this is the CSRF mitigation for all of those routes.
- **The HTTP-executed capability path** (processor and documents, which actuate via
  one structural `HTTP_POST` step instead of a browser replay) sends the same token
  as an `Authorization: Bearer` header from `tandem/replay/http_executor.py`.

---

## 8. Reporting a Vulnerability

If you discover a potential security flaw or vulnerability in Tandem's guard verification, lease coordination, or ledger integrity:
- Do not open a public issue on GitHub.
- Submit details and reproduction scripts to `security@tandem-financial.internal` (or repository maintainers).
- Vulnerability reports will be acknowledged within 24 hours with an initial assessment and remediation timeline.
