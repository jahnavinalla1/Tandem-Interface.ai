# Tandem: Technical Interviewer & Architectural Review Guide

This guide compiles anticipated questions, design trade-offs, and architectural
justifications for anyone evaluating Tandem. Every code reference below was checked
against the actual source at the time of writing — see the cited `file:line` for each
claim rather than taking it on faith.

---

## 1. Foundational Architecture & Philosophy

### Q1: Why not simply use an autonomous LLM agent (e.g. Browser-Use, Claude Computer Use, LangGraph) for production execution?
**Answer:**
Autonomous LLM agents are non-deterministic, high-latency, and prone to hallucination.
In financial systems, the central risk is:
> *"The money moved. The procedure didn't finish."*

If an agent crashes or receives a timeout after posting a provisional credit, its next
step is stochastic. It might re-run the whole flow and post credit twice, pick a
confusable customer from a fuzzy search, hallucinate that a failed transaction
succeeded, or simply cost \$0.05–\$0.20 and 3–10 seconds per browser step.

**Tandem's answer:** a **Zero-LLM Replay Invariant**. The LLM is quarantined to
**discovery** — exploring an unfamiliar UI once and compiling a typed, versioned,
hash-verified YAML capability. Production replay executes that artifact with raw
Playwright calls, asserting `llm_tracker.call_count == 0` in the test suite
(`tests/e2e/test_deterministic_replay.py`).

### Q2: Why not just write standard RPA (UiPath-style) or raw Playwright scripts?
**Answer:**
Two failures raw scripts don't solve:
1. **No effect semantics.** A raw script can't distinguish a benign click from an
   irreversible mutation. Tandem's `EffectClass.COMMIT` vs `READ`
   (`tandem/domain/effects.py`) makes that distinction structural, not incidental —
   every `COMMIT` capability is *required* to declare a precheck, postcheck,
   reconciliation strategy, and an adjacent structural guard, or artifact validation
   rejects it (`tandem/domain/capability.py`).
2. **Selector fragility with zero drift awareness.** Hardcoded selectors break the
   moment a second institution's UI differs. Tandem's surface overlays
   (`tandem/surfaces/overlays.py`) decouple semantic targets from DOM implementation —
   the *same* artifact replays against Institution Beta's differently-styled UI by
   swapping only the overlay, still with 0 LLM calls (`scripts/demo.py`'s
   `run_second_institution`).

---

## 2. Discovery — What the Model Sees and Does

### Q3: Walk me through exactly what happens during discovery. What does the model actually see?
**Answer:** `DiscoveryAgent.discover_provisional_credit()` (`tandem/discovery/agent.py:42`)
runs a bounded observe → decide → act loop, up to `max_cycles` (default 20):

1. **Observe** (`_observe`, `tandem/discovery/agent.py:133`): reads the page title, up
   to 12,000 characters of visible body text per frame, and a list of every
   interactive element (`a, button, input, select, textarea`) with its tag, id, name,
   type, visible text, and non-sensitive value (`password`/`hidden` inputs are
   excluded at the point of observation). This becomes a `BrowserObservation`
   (`tandem/discovery/provider.py:24`) — a bounded, typed snapshot, not raw HTML.
2. **Decide**: that observation, the stated objective, the declared inputs (with
   `redact_secrets()` applied), and the last 8 prior decision/result events are sent
   to `OpenAIResponsesProvider.decide()` (`tandem/discovery/provider.py:95`), which
   asks the model for exactly one structured decision — never free-form code or
   natural-language instructions the runtime has to interpret.
3. **The model's output is a validated Pydantic model, not raw text.**
   `DiscoveryDecision` (`tandem/discovery/provider.py:36`) only permits five actions —
   `NAVIGATE`, `FILL`, `CLICK`, `SUBMIT`, `FINISH` — and a `@model_validator` rejects
   structurally invalid decisions before they ever reach the browser: `FILL` without a
   declared `input_name`, `SUBMIT` without `is_mutating` + `container_selector` +
   `guard_ref`, any other action incorrectly marked mutating.
4. **Act**: the agent executes exactly that one decision against the real browser
   (`_execute_decision`, `tandem/discovery/agent.py:172`), takes a screenshot, and
   records everything via `TraceRecorder` before looping back to Observe.
5. Discovery ends when the model emits `FINISH`; the agent then reads the actual
   receipt (memo code, money-moved flag) from the page — the trace's success claim is
   grounded in the real page state, not the model's self-report.

### Q4: How does a successful discovery run get "recorded" and turned into something replayable?
**Answer:** Two things happen, both durable:

1. **Evidence, per run** (`TraceRecorder`, `tandem/discovery/recorder.py`): every cycle
   — the observation, the model's decision, the action actually executed, the result,
   and a screenshot — is written under `evidence/discovery/<run_id>/`, keyed by a
   UUID `run_id`. This is the audit trail of *how* the model found the flow.
2. **The compiled capability** (`CapabilityCompiler.compile()`,
   `tandem/discovery/compiler.py:44`): once discovery finishes, the compiler turns the
   trace's *executed actions* (not the model's raw reasoning) into a
   `CapabilityDefinition` — concrete Playwright steps with literal selectors,
   templated inputs (e.g. `{{input.member_id}}`), an effect spec (COMMIT/READ,
   idempotency key, precheck/postcheck/reconciliation), and a `scoped_guard` pinned to
   the container the model actually clicked in. The compiler asserts the trace's
   recorded actions match its durable provider-decision events before it will compile
   at all (`compiler.py:53`) — it can't silently drift from what really happened.
   The result is canonicalized, hashed (SHA-256), and written to
   `capabilities/compiled/<capability_id>.yaml` with the hash in both a header comment
   and a schema field. A checked-in example from a real run is at
   `capabilities/compiled/demo_post_provisional_credit.yaml`.

### Q5: How do you actually know the model isn't being called during replay?
**Answer:** Three independent layers, not just a docstring claim:
1. `llm_tracker` (`tandem/policy/telemetry.py`) is a single process-wide counter
   incremented only inside `OpenAIResponsesProvider.decide()`
   (`tandem/discovery/provider.py`) — the only code path that ever calls out to a
   model.
2. `DeterministicExecutor.execute()` (`tandem/replay/executor.py:45`) never imports or
   references the discovery provider at all — replay's action loop reads a compiled
   `CapabilityDefinition`'s steps and calls Playwright directly.
3. Every replay test explicitly asserts the counter is unchanged after execution
   (e.g. `assert llm_tracker.call_count == 0` in `tests/e2e/test_deterministic_replay.py`
   and in `scripts/demo.py`'s replay scenarios) — this isn't a claim you have to trust,
   it's a runtime invariant checked on every CI run.

---

## 3. Deterministic Replay & the Effect Protocol

### Q6: Walk me through the lifecycle of a COMMIT capability. How is double-credit prevented?
**Answer:** `EffectEngine.execute_capability()` (`tandem/replay/engine.py`) runs each
`COMMIT` capability through a fixed sequence:
1. **Precheck** (`execute_precheck()`, `tandem/replay/precheck.py:15`): an HTTP GET
   against the target's own state before touching the DOM. A positive match returns
   `ExecutionOutcome(category=BUSINESS_OUTCOME, code=ALREADY_APPLIED)` and the engine
   halts — nothing is clicked, nothing moves.
2. **Bounds check**: policy validates the amount against `capabilities/core/post_provisional_credit.yaml`'s declared `bounds` (currently `max_amount: 500.00`,
   `currency: USD`), enforced in `tandem/policy/rules.py`.
3. **Scoped guard** (`verify_control_scoped_guard()`, `tandem/replay/guards.py:11`):
   immediately before the mutating click, re-reads the *exact* member/account/amount
   values inside the immediate container of the submit control — not the search
   results list, not a cached value. A mismatch raises `EntityBindingMismatchError` or
   `AmountMismatchError`, which the executor maps to
   `OutcomeCategory.HARD_FAILURE` / `OutcomeCode.ENTITY_BINDING_MISMATCH` or
   `AMOUNT_MISMATCH` — zero money moved.
4. **Actuation**: the deterministic Playwright click.
5. **Postcheck** (`execute_postcheck()`, `tandem/replay/postcheck.py:14`): an
   independent HTTP GET against the target confirms the effect actually landed —
   Tandem never trusts a "Success!" page alone.
6. **Reconciliation on ambiguity** (`reconcile_commit_execution()`,
   `tandem/replay/reconciliation.py:11`): if the browser action itself failed or timed
   out after the guard passed, this re-runs the postcheck. If it can positively
   confirm absence, the outcome is `CONFIRMED_NOT_APPLIED` (safe to retry). If it
   can't determine either way, the outcome is `OutcomeCategory.UNCERTAIN_EFFECT` /
   `OutcomeCode.UNCERTAIN_EFFECT`, and the engine refuses to retry automatically —
   the case is escalated to a human instead.

### Q7: How does Tandem handle a network drop or 504 on a write?
**Answer:** Covered above (reconciliation step). The important design point: Tandem
never conflates "the browser action raised an exception" with "the effect didn't
happen." Those are different facts, and only the target system's own state (via
postcheck) can resolve which one is true.

### Q8: Why SQLite in WAL mode instead of Postgres or Redis for the ledger?
**Answer, honestly stated:**
1. **Zero-dependency crash resilience for a single-node demo.** A worker can
   atomically record its own intent before issuing a browser click without depending
   on a separate database service being reachable.
2. **WAL mode** (`PRAGMA journal_mode=WAL`, `busy_timeout=30000`,
   `tandem/ledger/database.py`) lets the operator console read concurrently while a
   worker appends events.
3. **This is not a "just change one connection string" story to Postgres**, and I
   won't claim otherwise: `tandem/ledger/database.py` hardcodes a `sqlite:///` URL,
   sets SQLite-specific PRAGMAs, and — more importantly — the append-only enforcement
   added to fix the audit's H-09 finding ("the append-only ledger claim is false") is
   implemented as **SQLite-specific triggers** using `RAISE(ABORT, ...)` syntax. A
   real multi-node deployment would need a genuine migration: either equivalent
   Postgres triggers/constraints, or moving immutability enforcement into the
   application layer. That's real, scoped follow-up work, not a config change.

### Q9: How does Tandem prevent a human specialist and automation from conflicting?
**Answer:** A single-owner lease with a fencing token per case
(`tandem/handoff/coordinator.py`). The operator console exposes
`POST /cases/{case_id}/claim_lease` and `POST /cases/{case_id}/release_lease`
(`tandem/api/app.py`, both admin-token-gated); if automation attempts to act on a
case a human currently owns, the engine raises `LeaseConflictError` before touching
the browser.

**Known gap, stated plainly:** `HandoffCoordinator.operator_clear_compliance()`
(`tandem/handoff/coordinator.py`) — the method that actually clears a compliance
interstitial on the shared browser session — is exercised directly in Python (see
`scripts/demo.py`'s `run_human_handoff`, which calls it in-process against the same
`page` object automation was using) but is **not yet wired to an authenticated HTTP
route** a remote operator-console user could trigger for that specific action. The
general brokered browser-session action API exists (see `AUDIT_REPORT.md`'s H-05
finding and its fix) for bounded actions on a claimed session; this specific
compliance-clear helper doesn't route through it yet. If I had another week, this is
one of the first things I'd finish.

---

## 4. What I'd Improve With Another Week

In priority order:
1. **Wire `operator_clear_compliance` through the HTTP browser-session action API**
   (see Q9) instead of only being callable in-process.
2. **A real Postgres-compatible ledger path** for the append-only trigger logic (see
   Q8), so the "swap the backing store" story is actually true, not aspirational.
3. **A second, independently-discovered capability** (e.g. `docs.send_notice` or
   `processor.file_chargeback` discovered live rather than hand-authored) to prove
   discovery generalizes beyond the one flow it's been run against so far — discovery
   has been exercised on `core.post_provisional_credit` only.
4. **Finish M-03** (`REMEDIATION_STATUS.md`): a few evidence/procedure abstractions
   from the original design are still only partially wired into the runtime.
5. **A real Docker build/run**, verified end-to-end. The Dockerfile's layer ordering
   was fixed and statically reviewed (H-12), but no Docker daemon was available in
   this environment to run an actual `docker build`.

---

## 5. Which Parts Were AI-Assisted

Asked directly, the honest answer: **this project was built with extensive AI
assistance** — using Claude Code throughout development, the independent audit
remediation, the demo-scenario debugging, and this documentation pass itself. That
includes writing the majority of the production code, the test suite, the audit
remediation fixes, and these docs.

What was *not* faked: the independent adversarial audit in `AUDIT_REPORT.md` found
real, specific defects (a race condition allowing double-credit, a guard that could be
bypassed by a hidden-field swap, an "append-only" ledger that was provably mutable,
unauthenticated admin routes, and more) against an earlier version of this code, and
`REMEDIATION_STATUS.md` tracks every fix with its own regression test and commit
hash — nothing is marked fixed without a passing verification command. The 8 demo
scenarios were run live, not just unit-tested, and several were found broken during
that live run and fixed in the open (see the "Fix scripts/demo.py" commit).

If asked in an interview: be upfront about the tooling. The engineering judgment —
what to fix, what's actually a limitation vs. solved, what to prioritize next — is the
part that should be defensible as your own regardless of which keystrokes typed the
code.
