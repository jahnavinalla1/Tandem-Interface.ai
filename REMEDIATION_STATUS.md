# Security and reliability verification

This document maps the final Tandem implementation to the security and reliability
issues exercised by the repository's adversarial tests. All items below are resolved
in the current submission. The complete test suite, preserved adversarial suite,
lint, type checks, container build, package build, and wheel smoke test run in CI.

| ID | Verified behavior | Implementation and evidence |
|---|---|---|
| C-01 | Discovery uses a real model-directed observe-decide-act loop | `tandem/discovery/agent.py`, the provider-backed bundle in `evidence/20260913T161444Z/`, and `tests/regression/test_provider_discovery.py` verify the loop and its recorded provenance. |
| C-02 | Concurrent workers cannot apply the same effect twice | The durable identity claim and fencing protocol are exercised by multi-process races in `tests/regression/test_process_safety.py`. |
| C-03 | Precheck failures stop a COMMIT | Typed inquiry outcomes fail closed for unavailable, malformed, mismatched, and ambiguous responses; covered by `audit_tests/test_adversarial.py` and effect-protocol tests. |
| C-04 | A COMMIT requires complete, structurally bound guard evidence | The executor requires the institution, member, account, case, amount, and currency from the owning form immediately before submission. |
| C-05 | Displayed and submitted values cannot silently differ | Bound-form validation and independent postcheck reject hidden-value substitution; covered by the adversarial and effect-protocol suites. |
| C-06 | Ambiguous submissions are reconciled safely | Execution phases preserve whether submission may have occurred; target inquiry resolves the effect or returns `UNCERTAIN_EFFECT` and fences retries. |
| C-07 | Crash recovery preserves obligations | Durable obligations precede irreversible actions and the event ledger rebuilds projections after injected process exits; see `tests/regression/test_crash_matrix.py`. |
| C-08 | Target effects survive service restarts | Core, processor, and document simulators use independent SQLite state and resume reconciles ledger state with target state; see `tests/regression/test_target_persistence.py`. |
| H-01 | Every mutating capability uses the same effect protocol | Credit, processor, and notice actions share claim, precheck, policy, guard, execution, postcheck, and reconciliation behavior; see `tests/regression/test_effect_protocol_unification.py`. |
| H-02 | Capability integrity is enforced | Canonical SHA-256 verification covers normalized artifact content, while schema versions, surfaces, and destinations are validated on load. |
| H-03 | Invalid COMMIT artifacts are rejected | Validation requires effect identity, precheck, postcheck, reconciliation, guarded mutation, and executable steps. |
| H-04 | Human ownership is enforced | Atomic leases include owner, expiry, heartbeat, and fencing token; stale owners cannot operate the page. |
| H-05 | Handoff preserves the live browser session | A brokered worker retains the same page and context while ownership transfers to an operator and back; see `tests/integration/test_browser_session_handoff.py`. |
| H-06 | The documented launcher and demos use running services | Simulator control goes through authenticated HTTP administration rather than test-only process state; verified by `tests/regression/test_launcher_demo_compatibility.py`. |
| H-07 | Work can enter through an operational service | The authenticated case API submits work to `CaseRunner`; startup recovery resumes eligible non-terminal cases while uncertain effects remain stopped. |
| H-08 | Alpha and Beta are independent institutions | Separate services, databases, branding, and DOM structures use one unchanged, hash-verified capability with trusted routing and overlays; see `tests/integration/test_second_institution.py`. |
| H-09 | Audit history is append-only and verifiable | SQLite triggers prohibit event mutation/deletion, events are hash chained, concurrent appends remain ordered, and projections can be rebuilt; see `tests/regression/test_event_ledger.py`. |
| H-10 | Mutation and operator routes require authentication | Services bind to localhost by default; administrative, operator, credit, chargeback, and notice mutations require the configured token and browser forms carry the protected value. |
| H-11 | A clean checkout can execute the quality gate | CI installs the locked development environment and runs lint, type checks, both test suites, the container build, package build, and wheel smoke test. |
| H-12 | The container installs reproducibly | The Dockerfile installs locked dependencies before source, then installs Tandem; CI performs a real image build and the Compose ledger uses a named volume. |
| M-01 | Monetary values use exact finite decimal arithmetic | Domain, policy, persistence, effect comparison, and simulator paths use validated, cent-quantized `Decimal` values. |
| M-02 | Deadlines account for calendar and timezone rules | Configurable rules support institution timezones, federal holidays, weekends, standard and extended periods, and overdue evaluation. |
| M-03 | Runtime abstractions are functional or removed | Discovery provenance, evidence, browser-session identity, and reconciliation settings participate in execution; unused decorative procedure/evidence abstractions were removed. |

## Current submission boundary

The implemented compiler targets the demonstrated provisional-credit web workflow.
Native desktop execution, arbitrary-task compilation, automatic tenant semantic
alignment, and a production multi-node control plane remain outside this prototype.
These boundaries are also described in `REPORT.md` and `SUBMISSION_CHECKLIST.md`.

## Verification

Run the complete local gate after following the README setup:

```sh
uv run ruff check .
uv run mypy tandem simulators
uv run pytest tests -q
uv run pytest audit_tests -q
make verify-assignment
```

`make verify-assignment` demonstrates successful replay, duplicate prevention, policy
denial, intervention, and handoff/resume without calling a model during replay.
