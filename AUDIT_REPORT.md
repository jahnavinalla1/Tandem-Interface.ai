# Tandem Independent Engineering Audit

Audit date: 2026-09-11  
Repository revision: `c0eb1ff` (`master`)  
Audit scope: specification, tracked source, configuration, tests, demos, clean setup, browser execution, persistence, concurrency, crash boundaries, security, packaging, and documentation.  
Production-code changes made: none. Independent checks are isolated under `audit_tests/`.

## 1. Executive Verdict

Tandem looks sophisticated, but several central claims are not actually implemented. Model-driven discovery is simulated, exactly-once protection fails under concurrency, guard enforcement can be bypassed, artifact integrity is not verified, crash safety has unprotected windows, same-session handoff is not a working operator-console feature, and the second-institution demo modifies the artifact and duplicates simulator code. This is a DEMO, not a strong or production-like prototype.

## 2. Overall Score

**32/100**

The score credits a real Playwright replay, a useful outcome taxonomy, SQLite persistence, a serial precheck, a real processor reconciliation happy path, and readable modular code. It is held down by failures in the exact areas the project presents as its differentiators.

## 3. Ship / No-Ship

**NOT READY**

## 4. Critical Findings

| Severity | Finding | Evidence | Impact | Required Fix |
|---|---|---|---|---|
| CRITICAL | Discovery is fake, not model-driven | `tandem/discovery/agent.py:44-175` increments `llm_tracker` and performs hardcoded selectors/actions. It imports no model client. It succeeds with no provider credentials. | The primary discovery claim and demo are false. | Integrate a real provider/agent, persist request/response evidence, and compile from observed model-directed actions. |
| CRITICAL | Two workers can both commit the same case | Independent race test returned two `COMPLETED` outcomes. `EffectEngine` prechecks before acquiring any effective exclusion (`tandem/replay/engine.py:63-112`), and `acquire_lease` overwrites owners (`tandem/ledger/repository.py:229-240`). | Duplicate money movement is possible. | Use an atomic DB claim keyed by idempotency key, reject active claims, hold ownership through reconciliation, and test with real concurrent processes. |
| CRITICAL | Precheck failure is treated as effect absence | `tandem/replay/precheck.py:33-54` swallows every exception and returns `None`; the engine then commits. Independent outage test failed. | An unavailable inquiry endpoint turns safety off. | Fail closed with `PRECHECK_UNAVAILABLE`; never actuate COMMIT until absence is positively established. |
| CRITICAL | Guard verification fails open and omits case binding | Missing member/amount observations are accepted (`executor.py:81-100`; `guards.py:32-43`). `expected_case_template` is never read. A real browser test posted `D-ACTUAL` while inputs declared `D-EXPECTED` and returned `SUCCESS`. | Wrong record/case commits can pass as success. | Require all declared observations, bind member/account/case/amount to the actual submitted form, and fail on absent or ambiguous evidence. |
| CRITICAL | Amount binding is vulnerable to a control/data split | The guard reads visible/data attributes, not the submitted hidden value. After showing `$340`, changing the commit form value to `$34` produced `COMPLETED`, posted `$34`, and raised balance only to `$1,274.50`. | Money can move for a different amount than authorized. | Read and lock the exact form/control values immediately before submission; postcheck the external amount and entity. |
| CRITICAL | Core ambiguous-effect reconciliation is unreachable in normal execution | `DeterministicExecutor.execute` catches generic exceptions and returns `HARD_FAILURE` (`executor.py:226-231`), so `EffectEngine`'s exception reconciliation (`engine.py:117-125`) normally runs only when tests mock the entire executor to raise. | A dropped response after a core commit can be misclassified and then mishandled on retry. | Preserve commit-phase exception type/boundary and force reconciliation before classification or retry. |
| CRITICAL | Crash safety has a durable deadline gap | Actual `os._exit(86)` immediately after `EffectEngine` persisted the credit left only the 10- and 45-day deadlines. Resume returned `SUCCESS` and sent a notice, but the ledger never contained `NOTICE_2_DAY`. The deadline is created later at `reg_e.py:249-253`. | The system can lose the regulated clock it claims to preserve. | Persist the notice obligation/deadline atomically with or before the effect intent; reconstruct missing obligations from durable effect facts. |
| CRITICAL | Target effects are not durable and restart reconciliation is absent | All simulator effects are process-local dictionaries (`simulators/*/state.py`). After core restart, resume skipped the credit from ledger state, returned `SUCCESS`, and left the external credit absent. | Ledger and target state can contradict each other while the workflow claims success. | Give simulators durable stores for the demo and reconcile ledger state against target state on every resume. |
| HIGH | Processor and notice COMMITs bypass the effect protocol | `reg_e.py:136-198` and `272-300` call HTTP endpoints directly with no staged intent, lease, general precheck, scoped guard, or safe restart path. | Duplicate chargebacks/notices and orphaned effects remain possible. | Express them as capabilities and run them through one effect protocol. |
| HIGH | Artifact integrity is cosmetic | Compiler writes a hash comment (`compiler.py:115-124`), while loader ignores it and overwrites `artifact_hash` (`capability.py:125-132`). A tampered selector loaded without error. Header and loader hashes differ. | Modified COMMIT artifacts execute without detection. | Canonicalize, sign/hash a defined payload, store the digest in schema, and reject mismatches at load. |
| HIGH | COMMIT schema permits non-executable artifacts | A COMMIT with complete metadata, a nominal guard, and zero steps validates. Guard execution depends on action `ASSERT_CONTAINER` or exact semantic text `Commit Button` (`executor.py:67-70`). | The schema does not enforce the behavior its metadata promises. | Validate non-empty executable steps and a mandatory guard directly adjacent to every mutating actuation. |
| HIGH | Human ownership is not enforced by the execution engine | `EffectEngine` always calls `acquire_lease(..., "AUTOMATION")`, which overwrites an active human owner. Independent lease test failed. There is no expiry or fencing token. | Human and automation can act concurrently. | Implement compare-and-swap lease acquisition, fencing tokens, TTL/renewal, and ownership checks before every browser action. |
| HIGH | Same-session handoff is not an operator-console feature | Only an in-process function accepts a Python `Page` object (`handoff/coordinator.py:73-115`). The API has claim/release routes but no route/session transport for clearing the interstitial. `browser_session_id` is never populated. | A remote operator cannot take over the claimed live session. | Add a browser-session broker/view, durable session identity, fenced controls, and an API/UI path that operates that same context. |
| HIGH | The documented launcher and handoff demo are incompatible | With `start_services.py` running separate processes, `demo.py --scenario human-handoff` reported initial `SUCCESS`, then timed out and exited 1. The demo modifies a local singleton (`demo.py:283-284`) instead of the running service. | README-reproducible demos are not reproducible in the documented runtime. | Drive failure switches through HTTP/admin fixtures and remove imports from `tests.server_utils`. |
| HIGH | There is no running automation service | `start_services.py:8-13` starts simulators and the dashboard only. `tandem/api/app.py` has no workflow-start route or worker. `RegEWorkflow` is invoked only by tests/demo. | The launched product cannot process a dispute. | Add an authenticated orchestration entry point and worker lifecycle, or clearly label the repository as a library-only demo. |
| HIGH | The second institution is neither a second instance nor the unchanged artifact | Beta is another route in the same app and shares the same state (`core_bank/app.py:491-647`). Tests/demo deep-copy and mutate the NAVIGATE URL (`test_second_institution.py:41-45`; `demo.py:338-342`). | Cross-institution reuse is overstated. | Run a separate instance/state store and make institution routing part of configuration/overlay without mutating the signed artifact. |
| HIGH | The “append-only ledger” claim is false | Cases, executions, intents, deadlines, and leases are updated in place (`repository.py:56-67`, `132-155`, `189-193`, `209-219`, `229-251`). Relationships allow cascade deletes (`models.py:35-40`). | History is mutable and cannot independently reconstruct every change. | Make immutable events authoritative; derive projections; prevent update/delete of audit facts. |
| HIGH | Operator and simulator mutations are unauthenticated | Services bind to `0.0.0.0`; reset/mode, direct credit, chargeback, notice, and lease routes require no auth or CSRF protection. | Any reachable client can alter financial/demo state and ownership. | Bind locally for demo, document the limitation, and add authentication/authorization/CSRF before any shared deployment. |
| HIGH | CI cannot execute its only gate from a clean sync | CI runs `uv sync` then `uv run pytest`; the same clean sequence failed with `pytest: program not found` because `dev` is an optional extra. CI also runs no Ruff or mypy. | The badge/gate does not establish a passing revision. | Use `uv sync --extra dev --frozen`, pin setup-uv, and add lint/type/coverage gates. |
| HIGH | Dockerfile fails before copying source | It copies only `pyproject.toml` and `uv.lock`, then runs `uv sync --frozen`; isolated reproduction failed because `README.md` was absent. Docker daemon was unavailable, so a full image build was not possible. | Documented container build is broken. | Copy build metadata/source first or use `uv sync --no-install-project`, then install the project after `COPY . .`. |
| MEDIUM | Monetary data uses binary floats and accepts NaN | Models and policy use `float`; `float("nan")` passes both bound comparisons (`policy/rules.py:20-40`). Independent test failed. | Invalid/non-finite values bypass policy; cents can round incorrectly. | Use `Decimal`, reject non-finite values, quantize currency, and enforce DB constraints. |
| MEDIUM | Deadlines omit holidays, 90-day cases, overdue evaluation, and institution timezone | `deadlines.py:7-21` skips weekends only and fixes cutoff at 17:00 UTC. Documentation claims federal holidays. No monitor marks `OVERDUE`. | Regulatory status can be wrong. | Add a jurisdiction/calendar abstraction, injected clock, overdue monitor, and supported-case rules. |
| MEDIUM | Evidence and procedure abstractions are dead | `EffectEvidenceRecord`, `capture_evidence`, `ProcedureDefinition`, reconciliation attempt count, expected case template, and browser session ID are defined but unused. | Architecture diagrams show controls the runtime does not exercise. | Remove decorative abstractions or wire them into the execution path with tests. |

## 5. Requirements Verification

| Category | Requirement | Source | Expected behavior | Implementation location | Status |
|---|---|---|---|---|---|
| MUST BUILD | Hostile core simulator | PDF p.3, section 5 | Frames/table UI, unstable IDs, seeded hostility | `simulators/core_bank/` | PARTIAL - one iframe, stable production selectors, exact-ID replay avoids fuzzy ambiguity |
| MUST BUILD | Second institution instance | PDF p.3, section 5 | A second instance of the core | Beta routes in `core_bank/app.py` | FAIL - same process/state, not a second instance |
| MUST BUILD | Surface abstraction | PDF p.3, section 5 | Separate semantic targets from driver | `tandem/surfaces/` | PASS for the one browser surface |
| MUST BUILD | LLM discovery | PDF pp.2-3 | A model drives first-time discovery | `discovery/agent.py` | FAIL - hardcoded script plus counter |
| MUST BUILD | Capability compiler | PDF pp.2-3 | Compile recorded discovery into typed/versioned artifact | `discovery/compiler.py` | PARTIAL - emits YAML, but synthesizes fixed metadata and no discovery provenance |
| MUST BUILD | Replay engine | PDF pp.2-3 | Deterministic artifact execution | `replay/executor.py`, `engine.py` | PARTIAL - real Playwright for core, incomplete step actions and failure protocol |
| MUST BUILD | Procedure ledger | PDF p.2 | Durable case record after every step | `ledger/` | PARTIAL - SQLite persists projections, but not append-only or complete at every crash boundary |
| MUST BUILD | Policy layer | PDF p.3 | Enforce risk/bounds before money moves | `policy/` | PARTIAL - max bound works; NaN, float, and metadata consistency fail |
| MUST BUILD | Live-session handoff | PDF pp.2-3 | Human operates the same live session with exclusive ownership | `handoff/`, API | FAIL as an end-user feature; only in-process test wiring works |
| MUST BUILD | Two capabilities | PDF p.3 | Exactly two meaningful capability artifacts | `capabilities/` | FAIL - one capability ID/artifact; other steps are hardcoded HTTP calls |
| MUST BUILD | One procedure | PDF p.3 | Reg E procedure across systems | `workflow/reg_e.py` | PARTIAL - runnable orchestration, but no service entry point and some steps are simulated reads |
| SHOULD BUILD | Case/transaction verification | PDF p.1 | Verify real member and disputed transaction | `reg_e.py:88-131` | PARTIAL - member API read is real; transaction and duplicate checks record unconditional success |
| SHOULD BUILD | Finalize/reverse on resolution | PDF p.1 | Resolution effect plus notice | State enum only | NOT IMPLEMENTED |
| STUBBED BY DESIGN | Thin processor portal | PDF p.3 | One screen and failure switch | `simulators/processor/` | PASS for stated thin scope |
| STUBBED BY DESIGN | Notice system | PDF p.3 | Clean seam, thin UI | `simulators/documents/` | PARTIAL - thin UI exists; duplicates/restart are unsafe |
| STUBBED BY DESIGN | Case tracker | PDF p.3 | Stubbed at a clean seam | None | NOT IMPLEMENTED |
| STUBBED BY DESIGN | Operator console UI | PDF p.3 | Bare UI over real transfer mechanism | `tandem/api/app.py` | PARTIAL - dashboard exists; same-session transfer mechanism is not exposed |
| DESIGNED ONLY | Capability registry | PDF p.3 | Explicitly not built | None | PASS as scoped omission |
| DESIGNED ONLY | Credential vaulting | PDF p.3 | Explicitly not built | None | PASS as scoped omission |
| DESIGNED ONLY | Desktop surface | PDF p.3 | Explicitly not built | Abstract surface only | PASS as scoped omission |
| DESIGNED ONLY | Queues | PDF p.3 | Explicitly not built | None | PASS as scoped omission, but means no automatic recovery |
| SAFETY | COMMIT requires precheck | PDF p.2, Move 1 | Schema rejects omission and runtime fails closed | Pydantic schema, `precheck.py` | PARTIAL - omission rejected; outage fails open |
| SAFETY | Effect executes exactly once | PDF p.2 | Serial, crash, and concurrent repeats do not duplicate | `EffectEngine`, intent table | FAIL under two-worker race |
| SAFETY | Guard is scoped to commit control | PDF p.2, Move 3 | Bind entity and amount from same logical control container | `executor.py`, surface | PARTIAL - displayed member/amount mismatch is caught; missing/case/submitted-value checks fail |
| SAFETY | Durable append-only reconstruction | PDF p.2, Move 2 | Kill anywhere and reconstruct facts/deadlines | SQLite ledger | FAIL for arbitrary crash points and external restarts |
| SAFETY | Business-day deadlines | PDF pp.1-2 | Persist 2- and 10-business-day clocks | `deadlines.py`, deadline table | PARTIAL - weekends only; notice deadline crash gap; no overdue monitor |
| SAFETY | Exclusive ownership | PDF p.3, Move 4 | Exactly one human/automation owner | Lease row/coordinator | FAIL - overwrite and race possible |
| SAFETY | Evidence and actors recorded | PDF pp.2-3 | Evidence, actor, capability version, errors, deadlines | Ledger models | PARTIAL - actor/version rows exist; browser evidence table is unused |
| DEMO | Discovery | PDF p.3, section 6 | Genuine model run produces committed artifact | Demo scenario 1 | FAIL |
| DEMO | New-case replay | PDF p.3 | Zero-model browser replay posts once | Demo scenario 2 | PASS |
| DEMO | Same-case replay | PDF p.3 | Existing target effect short-circuits | Demo scenario 3 | PASS only for serial/live-target case |
| DEMO | Transposed ID | PDF p.3 | Natural ambiguous search selects wrong row and guard blocks | Demo scenario 4 | PARTIAL - manually places browser on wrong record; full artifact never faces the ambiguous search |
| DEMO | Kill/resume | PDF p.3 | Real process death, durable deadline, no duplicate | Demo scenario 5 | FAIL as supplied; narrow independent process restart passes, adjacent hard-exit window loses deadline |
| DEMO | Handoff | PDF p.3 | Same live page, exclusive transfer, ledger record | Demo scenario 6 | FAIL in documented multi-process runtime |
| DEMO | Second institution | PDF p.3 | Same artifact plus overlay, drift signal | Demo scenario 7 | FAIL - artifact mutated; same state/app; duplicate UI workflow |
| DEMO | Uncertain effect | Requested eighth demo | Real after-submit ambiguity reconciles or escalates | Demo scenario 8 | PARTIAL - processor reconciliation is real; inconclusive core path mocks whole executor |

## 6. Build Verification

Clean export path was outside the repository and contained only `git archive HEAD` content.

| Command | Result | Exit | Problems |
|---|---|---:|---|
| `uv sync` | Base environment installed | 0 | Selected Python 3.14.3 because project says `>=3.12`; omitted dev tools |
| `uv run pytest -v` immediately after README sync | Failed | 2 | `pytest` program not found |
| `uv run playwright install chromium` | Succeeded | 0 | None observed |
| `uv sync --extra dev` (audit correction) | Succeeded | 0 | This command is not in README or CI |
| `uv lock --check` | Succeeded | 0 | Lock is internally valid |
| `uv build` | Built sdist and wheel | 0 | Wheel omits `capabilities/` and `scripts/`; installed package cannot run documented workflow/demo by itself |
| Isolated Docker dependency layer: copy only `pyproject.toml` + `uv.lock`, then `uv sync --frozen` | Failed | 1 | `README.md` missing before Docker copies source |
| Full `docker build` | Not testable | N/A | Docker client exists; daemon unavailable |
| `pip-audit` on installed environment | No known vulnerabilities | 0 | Local `tandem` package skipped because it is not on PyPI |

Additional reproducibility risks:

- Dependencies use broad lower bounds in `pyproject.toml`; reproducibility depends on always honoring `uv.lock`.
- CI installs `uv` version `latest`.
- Docker Compose bind-mounts a database file that does not exist in a clean checkout; Docker commonly creates a directory at a missing bind source.
- The package has no console entry point and omits runtime capability data.

## 7. Test Results

After the audit-only correction `uv sync --extra dev`:

- Existing suite: **45 passed, 0 failed, 1 warning**, 94.96 seconds.
- Coverage: **89% statement coverage** across `tandem` and `simulators`.
- Warning: Starlette deprecates the current `httpx` TestClient path.
- Ruff: **FAIL**, 50 findings (48 automatically fixable), including unused imports and import-order errors.
- mypy: **FAIL**, 8 errors in 3 files, plus unchecked untyped bodies.
- Package build: PASS, but incomplete artifact contents.

Why 45 passing tests do not prove the claims:

- Discovery tests assert the manually incremented counter, not provider traffic.
- Crash tests remain in one Python process and preserve simulator globals.
- The uncertain core effect test replaces `executor.execute` with a mock exception.
- Handoff passes a Python `Page` object directly in-process.
- No existing test runs two effect workers.
- No existing test makes precheck unavailable.
- No existing test tampers with an artifact hash.
- No existing test removes guard evidence or changes the submitted hidden value.
- No existing test restarts a simulator.
- “Integration” tests often inspect the same imported singleton the app uses, rather than an independent external state store.

## 8. Independent Adversarial Tests

Command: `uv run pytest audit_tests -q`  
Result: **10 failed out of 10** in 23.34 seconds.  
Interpretation: each test encodes a promised safety behavior, so each failure is a product defect, not a faulty expectation.

| Test | Expected | Actual |
|---|---|---|
| Tampered artifact | Loader rejects | Loaded successfully |
| Empty COMMIT artifact | Schema rejects | Validated successfully |
| Missing identity evidence | Guard fails closed | Guard returned successfully |
| Precheck transport outage | `PRECHECK_UNAVAILABLE` | Returned `None` (effect absent) |
| Human lease overwrite | Automation rejected | Owner overwritten |
| NaN amount | Policy denied | Policy passed |
| Wrong case in commit container | Hard failure, no post | `SUCCESS`; posted under wrong case |
| Submitted amount differs from displayed amount | `AMOUNT_MISMATCH`, no post | `SUCCESS`; posted `$34` after authorizing `$340` |
| Simulator restart before resume | Reconcile/restore or halt | Workflow returned `SUCCESS` while target credit was absent |
| Two workers, same case/effect | One commit, one short-circuit | Two `COMPLETED` outcomes |

Independent process evidence:

- An unhandled process exit at the repository's selected `kill_after_credit` hook resumed successfully in a fresh process while external simulators stayed alive: balance remained `$1,580.50`, memo unchanged, notice sent.
- A real forced exit (`os._exit(86)`) immediately after `EffectEngine` returned left the money and credit persisted externally but no `NOTICE_2_DAY` deadline in SQLite. Resume sent the notice and returned `SUCCESS`; the deadline remained absent.
- Restarting the in-memory core simulator erased the target effect; resume trusted its own ledger, skipped reconciliation, and returned `SUCCESS` with no external credit.

## 9. Eight Demo Results

| Demo | Command | Expected | Actual | Verdict | Evidence |
|---|---|---|---|---|---|
| Discovery | `uv run python scripts/demo.py --scenario discovery` | Real LLM drives UI | Hardcoded Playwright actions; counter says 7 | FAIL | `discovery/agent.py:44-175` |
| Replay | `... --scenario replay-new-case` | No LLM, real browser post | Posted and returned memo; no model path exists | PASS | External balance/credit checked by E2E and audit |
| Duplicate prevention | `... --scenario replay-same-case` | No second effect | Serial repeat returned `ALREADY_APPLIED` and balance delta 0 | PASS (narrow) | Fails concurrent/outage cases |
| Wrong entity | `... --scenario transposed-id` | Natural ambiguous selection blocked | Demo manually navigates to wrong confirmation page and trims artifact to one step | PARTIAL | It proves the comparison but stages the failure |
| Crash/recovery | `... --scenario crash-resume` | Hard process death and durable reconstruction | Catches a normal exception in same process; separate hard-exit found deadline loss | FAIL | `demo.py:225-257`; independent exit 86 |
| Human handoff | `... --scenario human-handoff` | Operator controls same live session | Works only in-process; failed with documented launcher (exit 1) | FAIL | Local singleton switch and direct `Page` injection |
| Second institution | `... --scenario second-institution` | Same artifact via overlay | Deep-copy mutates URL; same app/state; duplicated routes | FAIL | `demo.py:338-342`; `core_bank/app.py:491-647` |
| Uncertain effect | `... --scenario uncertain-effect` | Real ambiguity reconciled/escalated | Processor after-submit 504 reconciles; inconclusive core case mocks executor | PARTIAL | `demo.py:416-424`; executor swallows real exceptions |

`--scenario all` printed 8/8 success and exited 0 in 39.37 seconds when it started simulators as same-process daemon threads. That output is not reliable proof. With the documented separate launcher already running, the handoff scenario exited 1.

## 10. Core Claim Verification

**ZERO-LLM REPLAY: PASS**  
The replay import/call graph contains no model client, replay ran with empty keys, and selector failure returns drift rather than invoking AI. The repository counter alone is weak, but independent static and runtime checks support this narrow claim.

**DOUBLE CREDIT PREVENTION: FAIL**  
Serial precheck passes. Two concurrent workers both completed, and precheck outage fails open.

**WRONG ENTITY PREVENTION: FAIL**  
A visible member mismatch is caught, but absent observations pass, case is never checked, and the actual submitted amount can diverge from visible guard data.

**CRASH RECOVERY: FAIL**  
One selected crash point resumes. Arbitrary crash safety, durable notice obligations, target restart consistency, and automatic resumption do not hold.

**UNCERTAIN EFFECT HANDLING: FAIL**  
Processor 504 reconciliation exists, but the core executor converts real browser/transport exceptions before the engine can reconcile them.

**DURABLE LEDGER: FAIL**  
SQLite rows persist, but the ledger is not append-only, evidence is unused, a deadline can vanish across a real crash boundary, and projections can contradict the target.

**SAME-SESSION HUMAN HANDOFF: FAIL**  
An in-process function can reuse a `Page`; the operator API cannot take over that page and ownership is not enforced.

**SECOND-INSTITUTION REUSE: FAIL**  
The test changes the artifact and targets a second route sharing the first institution's process and state.

## 11. Architecture Review

Actual runtime architecture:

```text
demo.py / pytest (only workflow callers)
        |
        v
RegEWorkflow
  |-- direct HTTP -> member lookup
  |-- synthetic success rows -> transaction and duplicate checks
  |-- direct HTTP -> processor COMMIT
  |-- EffectEngine -> Playwright -> core-bank COMMIT
  |-- direct HTTP -> notice COMMIT
  `-- mutable SQLite records/events

start_services.py
  |-- core simulator (in-memory state)
  |-- processor simulator (in-memory state)
  |-- notice simulator (in-memory state)
  `-- dashboard/API (no workflow worker)
```

| Dimension | Score | Reason |
|---|---:|---|
| Separation of concerns | 6/10 | Modules are easy to navigate, but the workflow bypasses the effect engine for most effects. |
| Domain modeling | 4/10 | Useful enums/models; several are decorative and money is float. |
| Failure recovery | 3/10 | Narrow resume works; no automatic worker recovery, missing crash boundaries, no target restart reconciliation. |
| Idempotency | 2/10 | Serial target lookup only; concurrent and outage behavior is unsafe. |
| Durability | 3/10 | SQLite persists some state; external effects are volatile and audit facts are mutable/incomplete. |
| Browser abstraction | 5/10 | Real surface/overlay split; guard coupling to semantic text and incomplete actions make it fragile. |
| Discovery/replay separation | 4/10 | Replay is model-free, but discovery is not a model system. |
| Human handoff | 2/10 | In-process proof only; no fenced session broker. |
| Testability | 6/10 | Components are small and injectable, but globals/ports/process assumptions undermine isolation. |
| Maintainability | 5/10 | Readable layout; duplicated beta routes and divergent effect paths will drift. |
| Security model | 2/10 | Safety vocabulary is strong; actual auth, integrity, fencing, and redaction are absent. |
| Observability | 3/10 | Events/outcomes exist; evidence capture and durable operational telemetry do not. |

The repository is over-abstracted in places: `ProcedureDefinition`, evidence models, reconciliation configuration, and browser session IDs exist mainly to support the architecture narrative. Conversely, the difficult behavior is implemented inline in a 315-line workflow using direct HTTP calls.

## 12. Code Quality Review

| Dimension | Score | Reason |
|---|---:|---|
| Correctness | 3/10 | Core safety invariants fail adversarial execution. |
| Readability | 6/10 | Clear names and short modules, though comments overstate behavior. |
| Typing | 4/10 | Type hints are common; mypy fails with 8 errors and many bodies are unchecked. |
| Error handling | 2/10 | Broad exception swallowing changes “unknown” into “absent” and suppresses postcheck failures. |
| Naming | 6/10 | Generally clear, but names such as `Thread-safe`, `append-only`, and `DiscoveryAgent` are inaccurate. |
| Modularity | 5/10 | Good directory boundaries; runtime bypasses them. |
| Async correctness | 4/10 | Simulators use async endpoints, but mutable globals have no synchronization and orchestration is synchronous/blocking. |
| Persistence correctness | 3/10 | WAL is enabled; transaction design and source-of-truth semantics are incomplete. |
| State management | 3/10 | Explicit enum exists; direct status writes and volatile simulator globals bypass guarantees. |
| Test quality | 4/10 | Happy paths use real browsers; central claims rely on mocks/process-local state. |
| Documentation | 2/10 | Extensive but materially misleading in high-risk claims. |
| Maintainability | 5/10 | Small enough to change, but duplicated paths and false abstractions increase risk. |

Ruff and mypy are configured/available but not clean. No production TODO or `NotImplementedError` was found; the more serious issue is completed-looking code that is not connected to runtime behavior.

## 13. Security Review

Actual security defects:

- No authentication or authorization on operator ownership changes or simulator mutations.
- No CSRF protection on form POSTs.
- Services bind to all interfaces by default in the launcher, ignoring the safer configured host.
- Artifact tampering is not detected despite a claimed integrity boundary.
- Full member IDs and financial inputs are stored/displayed; `SECURITY.md` claims structured masking that does not exist.
- Lease ownership can be overwritten and has no fencing token, TTL, or stale-owner recovery.
- The global replay counter is not thread-safe despite its docstring and is not comprehensive instrumentation.
- Capability steps accept arbitrary navigation URLs and selectors. There is no signed allowlist boundary.

Prototype-acceptable limitations if documented honestly:

- No vault/MFA integration.
- Bare simulator login/session model.
- SQLite for a single-node demo.

Positive findings:

- No committed API keys, passwords, private keys, or real financial records were found.
- YAML uses `safe_load`.
- SQL statements use ORM expressions rather than string concatenation.
- Dynamic HTML generally uses `html.escape`.
- No Playwright orphan process was detected after the audit runs, including a forced worker exit.
- Dependency audit found no known third-party vulnerabilities in the installed environment.

## 14. Concurrency Review

Concurrency safety is BROKEN.

- Precheck occurs before lease acquisition.
- `acquire_lease` is an unconditional upsert-style overwrite.
- No lease state is checked by `EffectEngine` before or during browser actions.
- The unique idempotency-intent row does not reserve execution: an existing `STAGED` or `COMMITTED` intent is returned and ignored.
- No conditional update, `BEGIN IMMEDIATE`, row version, fencing token, or target-side atomic idempotency token exists.
- Two independent sessions reached the commit barrier and both returned `COMPLETED` for the same case/effect.
- Two operator claims are checked in Python, not by an atomic database predicate, so concurrent claims can race.
- Simulator state classes call themselves thread-safe but use ordinary mutable dictionaries and floats with no locks.

## 15. Data Integrity Review

- Monetary values are floats in Pydantic inputs, SQL `Float` columns, forms, and simulator state.
- Existing case identity/amount is not compared with new `run_case` inputs. A resumed caller can supply different member/amount values to downstream steps.
- Core pre/postcheck looks up by `case_id` only and does not verify member, account, amount, currency, or status.
- `post_credit` overwrites `credits[case_id]` while incrementing balance every time, hiding duplicate history.
- Provisional credits are not appended to member transaction history, so “transaction history” cannot independently prove movement.
- Processor and notice dictionaries also overwrite by case, masking duplicate submissions.
- A resumed ledger can report `money_moved=True` when the target has no effect after restart.
- Deadlines have no uniqueness constraint; duplicate deadlines are possible.
- The 2-day deadline can be omitted permanently at a crash boundary.
- Event payloads are JSON strings, but the UI JSON-encodes them again rather than parsing them.

## 16. Browser Automation Review

What works:

- Replay uses a real Chromium browser and iframe-aware Playwright locators.
- Primary selectors use stable names/classes rather than generated IDs.
- Ordered fallback selectors and basic drift messages exist.
- A displayed member/amount mismatch in the confirmation panel is rejected.

What is weak or broken:

- The “hostile” IDs are irrelevant because the artifact intentionally uses stable selectors.
- Full-ID search returns only the exact member; the two confusable IDs appear only for a shorter query the artifact never uses.
- The simulator has one iframe, not the claimed nested frameset.
- Discovery clicks the first result without identity reasoning.
- `WAIT_FOR`, `ASSERT_CONTAINER`, `READ_TEXT`, and `SELECT_FRAME` exist in the schema but are not implemented in the executor dispatch.
- The frame fallback silently switches to top-level page, which can broaden selector scope.
- Guard enforcement is triggered by an exact human label (`"Commit Button"`).
- Post-action receipt and money markers are optional; absence still returns `SUCCESS`.
- Click waits swallow load-state timeouts.
- No evidence is captured or persisted before/after COMMIT.
- The Beta overlay replaces selector lists instead of layering institution overrides with verified semantic constraints.

## 17. Documentation Accuracy

| Claim | Verdict | Evidence |
|---|---|---|
| “Genuine model-driven discovery” | FALSE | Hardcoded script plus local counter |
| “Zero-LLM replay” | TRUE for current replay path | No model imports/calls in replay; works without keys |
| “Exactly once” | FALSE | Two-worker duplicate execution |
| “Append-only ledger” | FALSE | Multiple rows updated in place; cascade deletes enabled |
| “Kill anywhere, crash-safe” | FALSE | Hard-exit gap loses notice deadline |
| “Same live browser session handoff” | PARTIALLY TRUE internally, FALSE as product feature | Same `Page` used in one process; no operator-console session transfer |
| “Single-owner lease” | FALSE | Active human owner can be overwritten |
| “Cryptographic artifact verification” | FALSE | Hash comment ignored; tampered artifact loads |
| “Second institution instance” | FALSE | Same app and in-memory store |
| “Same artifact on second institution” | FALSE | NAVIGATE step is mutated before replay |
| “Hostile fuzzy full-ID replay” | MISLEADING | Full ID returns one result; ambiguity is manually staged |
| “Federal banking holidays excluded” | FALSE | Weekends only |
| “PII masking” | FALSE | Full member IDs appear in records/UI/outcomes |
| “14-step effect protocol” | MISLEADING | Engine has six broad phases; processor/docs bypass it |
| “Production grade / production ready” | FALSE | Safety, security, runtime, CI, and container defects |

## 18. CI/CD Review

- Workflow has one job and only invokes pytest.
- Clean `uv sync` omits pytest, so the test command is not reproducible.
- Ruff and mypy failures are not gated.
- No coverage threshold, package install test, artifact-hash test, Docker build, or concurrency test exists.
- Browser dependencies are installed correctly in principle.
- No `continue-on-error`, `|| true`, or equivalent failure suppression was found.
- `setup-uv` uses `version: latest`, weakening toolchain reproducibility.
- No migration/schema compatibility check exists.
- Dockerfile order is broken; Compose persistence is unsafe for a missing host DB file.

## 19. Dead/Unused/Placeholder Code

- `tandem/domain/procedure.py`: unused declarative procedure model.
- `EffectEvidenceRecord`: never written or read.
- `Surface.capture_evidence`: implemented but never called.
- `expected_case_template`: declared/compiled, never enforced.
- `source_discovery_run_id`: committed artifact is `null`; compiler never sets it.
- `browser_session_id`: column exists, never populated.
- `ReconciliationSpec.strategy` and `max_inquiry_attempts`: never used.
- `OutcomeCode.POSTCHECK_UNCERTAIN` and several errors/outcomes are unused.
- `core_bank_2_port`/URL: configured but Beta runs on the first port.
- `ProcedureCaseRecord.events/executions` cascade deletion conflicts with immutable-ledger claims.
- Several schema step actions have no executor implementation.
- Compensation capability is named but not implemented.
- Transaction lookup and duplicate dispute check record success without querying a system.
- `FINAL_REPORT.md` and `PROJECT_STATUS.md` function as unsupported marketing artifacts, not evidence.

## 20. Missing Tests

Logical coverage missing despite 89% statement coverage:

- Real provider discovery request and captured provider response.
- Replay with model packages/keys disabled and global import/network instrumentation.
- Tampered/unsupported artifact versions and canonical hash verification.
- Empty/malformed COMMIT steps and guard adjacency.
- Missing member/account/case/amount observations.
- Submitted hidden value diverging from visible guard value.
- Precheck 500/timeout/malformed response.
- Real browser failure after server applies core effect but before receipt.
- Two processes committing the same effect.
- Policy change between precheck and commit.
- Crash before/after every durable write, including notice deadline creation.
- Core, processor, notice, browser, backend, and DB restarts in combinations.
- DB locked during intent, completion, deadline, and handoff writes.
- Processor and notice duplicate/restart behavior.
- Stale leases, fencing, process death while human owns, and two simultaneous humans.
- Operator-console takeover of a real shared browser session.
- Federal holidays, month/year boundaries, timezone/DST, 90-day cases, overdue marking.
- Authentication, authorization, CSRF, and direct simulator endpoint exposure.
- Wheel/container/Compose smoke tests.
- Browser cancellation and resource leak tests.

## 21. Top 10 Fixes Before Submission

1. Replace fake discovery with one real, recorded model-driven run and committed evidence/provenance.
2. Make COMMIT ownership/idempotency atomic with a durable reservation and fencing token; prove it with two processes.
3. Make precheck/postcheck fail closed and verify case, entity, account, amount, currency, and external status.
4. Bind guards to the actual submitted values/control and require every observation; remove semantic-label triggering.
5. Redesign crash ordering so obligations/deadlines exist before money can move; test every crash boundary with `os._exit`/process kill.
6. Route processor and notice effects through the same protocol, with their own prechecks and reconciliation.
7. Implement real same-session operator takeover or explicitly remove the claim.
8. Make simulator effects durable and independently restartable; reconcile target and ledger on resume.
9. Enforce artifact hash/signature/version at load and package capability data correctly.
10. Repair README/CI/Docker, add authentication boundaries, make Ruff/mypy clean, and gate adversarial tests.

## 22. What Is Actually Strong

- The core replay path is genuinely deterministic Playwright and contains no LLM decision loop.
- Serial same-case precheck works while the core inquiry endpoint is healthy.
- The confirmation-container comparison catches a present, explicit member/amount mismatch.
- The processor simulator realistically applies an effect before returning 504, and the direct workflow inquiry can reconcile it.
- SQLite WAL and foreign keys are actually enabled.
- A narrow process-boundary restart works when the credit completion and notice deadline were already committed and target services remain alive.
- Outcome categories clearly distinguish business, recoverable, hard, uncertain, and human cases.
- The codebase is small, readable, and reasonably separated by domain.
- HTML interpolation is generally escaped, YAML loading is safe, and no secrets were committed.
- Happy-path browser tests exercise real Chromium rather than only mocks.

## 23. What I Should NOT Claim In The Interview

Do not claim any of the following; the code or audit directly contradicts them:

- “Discovery is driven by an LLM.”
- “Tandem guarantees exactly-once effects.”
- “Two workers cannot double-credit a case.”
- “A precheck outage safely halts execution.”
- “The guard binds member, case, account, and exact submitted amount.”
- “The ledger is append-only.”
- “The system is crash-safe if killed anywhere.”
- “All regulatory deadlines survive every crash boundary.”
- “Uncertain core-browser effects always reconcile before retry.”
- “The operator console transfers the same live browser session.”
- “Leases atomically guarantee one owner.”
- “Institution Beta is a second instance using the unchanged artifact.”
- “Capability hashes are verified at runtime.”
- “The simulator forces replay to solve ambiguous full-ID search.”
- “The deadline engine handles federal holidays.”
- “Audit logs mask PII.”
- “The launched application processes disputes.”
- “CI, Docker, and the README clean setup are working.”
- “45 passing tests make this production grade.”
- “Tandem is production ready.”

## 24. What I CAN Confidently Claim

- “I built a deterministic Playwright replay for one provisional-credit UI flow, and replay does not import or call an LLM.”
- “A healthy target-side case lookup prevents a serial duplicate replay.”
- “The browser flow checks displayed member and amount in the final confirmation container when those fields are present.”
- “SQLite stores case, execution, event, deadline, intent, and lease records with WAL enabled.”
- “A fresh Python process can resume downstream notice work after a selected post-credit checkpoint while simulators stay alive.”
- “The processor simulator can model apply-then-504, and the workflow can confirm it via inquiry.”
- “An in-process handoff proof reuses the same Playwright `Page`, but it is not yet an operator-console/session-broker implementation.”
- “A Beta UI variant can run after mutating the target URL and supplying selector/container overrides.”
- “The existing 45 tests pass after installing the undeclared-in-README dev extra; independent adversarial tests currently fail.”
- “This is an effect-aware automation demo whose safety architecture still needs substantial implementation.”

## 25. Interview Questions

| Question | Why I would ask it | What the code actually does | Best honest answer |
|---|---|---|---|
| 1. Where is the real LLM call in discovery? | Core differentiator | Nowhere; a counter wraps hardcoded Playwright | “It is simulated telemetry today; real model discovery is P0.” |
| 2. What prevents two workers passing precheck together? | Exactly-once claim | Nothing effective; both can commit | “The current lease/intention design is insufficient; I reproduced the race.” |
| 3. What happens when precheck is unavailable? | Fail-safe behavior | Exception swallowed, commit proceeds | “That is a critical bug; COMMIT must fail closed.” |
| 4. Which exact value does the amount guard bind? | TOCTOU safety | Visible/data attribute, not submitted hidden input | “It does not yet bind the final submitted value; the audit posted 34 after displaying 340.” |
| 5. How is case identity verified? | Wrong-effect protection | `expected_case_template` unused | “Case binding is declared but not enforced.” |
| 6. What is the crash-consistency write order? | Central problem | Intent/execution commit, browser effect, result commit, then workflow deadline | “There is a deadline gap after the effect; I need an obligation-first design.” |
| 7. Is the ledger really append-only? | Auditability | Events append, most other rows mutate | “No. It is a mutable relational state store with an event table.” |
| 8. How do you recover if the core simulator restarts? | Source-of-truth consistency | Target loses data; ledger trusts itself | “The current simulator is volatile and resume does not reconcile; the claim requires durable target state.” |
| 9. How does an operator access the same session? | Handoff claim | Only direct Python `Page` injection | “The proof is in-process only; no remote session broker exists.” |
| 10. What fences automation after human takeover? | Split brain | Engine overwrites human lease | “Nothing reliable today; fencing tokens and CAS are required.” |
| 11. Why do processor and notice bypass EffectEngine? | Architecture consistency | Inline direct HTTP | “They were demo shortcuts and invalidate a uniform effect protocol.” |
| 12. How do you know a core timeout reconciles? | Uncertain effects | Executor swallows real exception; test mocks executor | “We do not know; that path is not genuinely tested or correctly wired.” |
| 13. Is Beta the same artifact and a separate institution? | Reuse claim | URL mutated; same app/state; duplicate routes | “No. It is a UI variant demo, not proven cross-institution reuse.” |
| 14. How are capability artifacts authenticated? | Supply-chain safety | Hash comment ignored | “They are not; current hashing is descriptive only.” |
| 15. What is required before a bank deployment? | Production judgment | No auth, vault, durable targets, distributed lock, migrations, monitoring, recovery drills | “Substantial P0/P1 work; I would classify this as a demo.” |

## 26. Final Verdict

**NO**, I would not submit this project to interface.ai today in its current form.

The repository's presentation is more mature than its runtime guarantees. The strongest pitch points - real discovery, exactly-once concurrency, arbitrary crash safety, immutable audit history, same-session operator handoff, and second-institution reuse - are false or materially incomplete. A strong interviewer will find these gaps quickly, and the misleading demos/docs make the risk worse than simply having an unfinished prototype.

Classification: **DEMO**.

It is not a TOY because it contains real browser execution, persistence, a multi-system happy path, and some useful failure semantics. It is not a STRONG PROTOTYPE because the central safety invariants fail under realistic adversarial conditions.

## Prioritized Remediation Plan

No remediation has been applied. This plan is for approval after the audit.

### P0 - MUST FIX BEFORE SUBMISSION

1. Implement genuine provider-backed discovery and commit one auditable discovery trace with prompts, model identity, browser observations/actions, evidence, and source run ID.
2. Introduce an atomic idempotency reservation/lease with fencing and a state machine such as `ABSENT -> CLAIMED -> APPLIED/UNCERTAIN`; make all workers honor it.
3. Fail closed on unavailable/malformed precheck and postcheck; verify the full effect tuple, not only case ID.
4. Make guard evidence mandatory and bind the exact submitted case/member/account/amount/currency immediately before commit.
5. Correct exception propagation so real post-submit browser failures enter reconciliation.
6. Move the notice obligation/deadline before the money effect or derive it durably from the effect intent; process-kill test every write boundary.
7. Route processor and notice through the effect engine and give them durable duplicate prevention.
8. Make simulator state durable and independently restartable; add target-vs-ledger reconciliation on resume.
9. Replace unconditional lease overwrite with atomic CAS + fencing; block automation while a human owns the session.
10. Remove or correct false documentation and demo output immediately.

### P1 - STRONGLY RECOMMENDED

1. Build a real session broker/operator takeover flow, or explicitly scope handoff to an in-process proof.
2. Make Beta a separate service/store and keep the signed artifact immutable; move endpoint routing into trusted configuration.
3. Enforce artifact version, canonical hash/signature, provenance, and allowlisted targets.
4. Use `Decimal` end-to-end and reject non-finite values at API/domain/DB boundaries.
5. Make immutable ledger events authoritative and rebuild projections from them.
6. Add holiday/timezone/90-day rules, overdue evaluation, and deadline monitoring.
7. Add authenticated/authorized operator endpoints, CSRF protection, safe bind defaults, and PII redaction.
8. Add a real orchestration entry point/worker with restart scanning and observable recovery.
9. Fix README setup, CI extras, Ruff, mypy, Docker layer order, Compose persistence, and wheel data inclusion.
10. Replace global singleton tests with independently running processes and externally queried state.

### P2 - NICE TO HAVE

1. Remove unused abstractions or implement evidence capture, procedure definitions, reconciliation policies, and browser session IDs.
2. Add schema migrations, retention policy, event integrity chaining, and operator audit exports.
3. Add browser cancellation/resource stress tests and memory/process telemetry.
4. Reduce duplicated simulator HTML through templates while preserving intentionally different DOMs.
5. Add performance measurements only after correctness; remove unsupported benchmark comparisons until measured independently.

