# Architecture Research & Open-Source Technology Selection

This document evaluates the relevant open-source projects, detailing what ideas Tandem adopts, what is deliberately rejected, and why.

---

## 1. browser-use/browser-use

**Purpose:** Model-driven browser interaction and discovery.

### What Idea We Use:
- **Vision/DOM-Guided Action Discovery:** Utilizing LLM reasoning to explore an unfamiliar or hostile user interface during initial setup.
- **Action Primitive Recording:** Extracting semantic actions (navigate, locate, fill, click, verify) along with surrounding contextual cues (labels, parent elements, values) during the exploration run.

### What We Do NOT Use:
- **LLM in the Production Replay Loop:** We do **not** invoke an LLM during standard capability replay or commit execution.
- **Dynamic Decision-Making During Commit:** An LLM must never improvise clicks or selections when real money or compliance deadlines are at stake.

### Why:
In financial workflows, nondeterministic action selection is unacceptable. Discovery is a one-time compile step that generates a deterministic capability artifact. In replay, `llm_call_count == 0` is an invariant enforced by automated tests.

---

## 2. microsoft/playwright-python

**Purpose:** Deterministic browser execution and replay.

### What Idea We Use:
- **Headless and Headed Chromium Automation:** Playwright's robust cross-platform browser engine.
- **Frame-Aware Selectors:** Navigating framesets, iframes, nested tables, and shadow DOM elements commonly found in legacy banking applications.
- **Network and Event Interception:** Precise control over request/response lifecycles, wait states, and timing for deterministic assertions.

### What We Do NOT Use:
- **Raw/Unscoped Global Locators:** We reject global locators like `page.locator("text=Submit")` or `page.get_by_text(...)` without container scoping.
- **Blind Auto-Waiting as Safety:** Playwright's auto-wait only checks if an element is clickable, not whether the data inside the row belongs to the target member.

### Why:
Playwright is the gold standard for browser interaction, but in legacy banking, "the click landed" is insufficient. Playwright is used as the underlying driver, strictly commanded by Tandem's container-scoped guards and surface abstraction.

---

## 3. pydantic/pydantic (v2)

**Purpose:** Capability schemas, validation, and serialization.

### What Idea We Use:
- **Strict Typed Schemas:** Defining typed inputs, outputs, effect metadata, bounds, locator candidates, and safety policies.
- **Schema Validation at Authoring/Compile Time:** Failing immediately if a `COMMIT` capability is defined without required safety metadata (`precheck`, `postcheck`, `idempotency_key`, `bounds`).
- **Data Coercion and Bound Enforcement:** Ensuring amounts are exact floats/decimals, currency is `"USD"`, and identifiers conform to strict formats.

### What We Do NOT Use:
- Loose dictionaries or unstructured YAML blobs without runtime validation.

### Why:
Financial safety demands strict contract enforcement before any browser session starts. Pydantic v2 is fast, natively supports JSON Schema export, and guarantees runtime correctness.

---

## 4. fastapi/fastapi

**Purpose:** Thin application, operator interface, and API layer.

### What Idea We Use:
- **Lightweight Asynchronous HTTP Endpoints:** Exposing endpoints for case ingestion (`POST /cases`), status lookup (`GET /cases/{case_id}`), audit log timeline (`GET /cases/{case_id}/events`), and human handoff actions (`POST /cases/{case_id}/handoff`).
- **Server-Rendered Minimal UI (HTML/Jinja):** A clean, simple operator screen allowing an operator to inspect live sessions, take control, complete steps, and release control.

### What We Do NOT Use:
- Heavy single-page frontend applications (React/Next/Vite with state stores) that distract from core distributed systems safety.
- Complex authentication gateways or enterprise microservice meshes for the prototype.

### Why:
FastAPI delivers high-performance async endpoints and integrates seamlessly with Pydantic v2 models. Bare HTML/Jinja provides an immediate, dependency-free operator console.

---

## 5. sqlalchemy/sqlalchemy (2.x)

**Purpose:** Procedure event ledger persistence.

### What Idea We Use:
- **Append-Only Relational Schema:** Mapping immutable ledger events, capability executions, effect intents, evidence captures, leases, and deadlines.
- **SQLite Engine with WAL (Write-Ahead Logging):** Crash-safe local persistence that survives hard process kills (`kill -9` / `sys.exit`).

### What We Do NOT Use:
- Complex ORM relationship auto-cascades or mutability tracking. The ledger is fundamentally an append-only event log.
- External database server dependencies (Postgres/MySQL) for local development and demos.

### Why:
SQLite requires zero external setup, provides ACID guarantees with WAL mode, and makes crash-recovery testing completely reproducible and deterministic.

---

## 6. pytest-dev/pytest & pytest-asyncio

**Purpose:** Reproducible failure scenarios and automated verification.

### What Idea We Use:
- **Test-Driven Safety Matrix:** Unit tests for schema validation, guards, and bounds; integration tests for simulator flows and SQLite restarts; end-to-end tests for all demo scenarios.
- **Process Crash Simulation:** Testing crash recovery by killing processes or injecting boundaries after specific effects, then restarting against the persistent SQLite file.
- **Zero-LLM Assertion:** Instrumenting replay tests to assert `llm_call_count == 0`.

### What We Do NOT Use:
- Flaky end-to-end tests with non-deterministic time delays or external web dependencies.

### Why:
Pytest provides powerful fixtures, parametrized tests, and clear failure diffs essential for verifying edge cases like transposed IDs, timeout drops, and crash resumption.

---

## 7. temporalio/sdk-python (Reference Only)

**Purpose:** Study durable execution, retries, and workflow recovery.

### What Idea We Use (Conceptual):
- **Durable State Reconstruction:** The idea that a workflow's history is the source of truth, and recovering after a crash means replaying history up to the point of failure rather than restarting side-effects.
- **Activities vs Workflows:** Distinguishing pure orchestration (the state machine) from external, potentially non-idempotent side effects (capabilities).

### What We Do NOT Use:
- The Temporal server daemon, Temporal Python SDK, or Cassandra/Postgres backend.

### Why:
Running a full Temporal cluster is heavy overkill for this project and adds unnecessary operational overhead. We implement a lightweight, self-contained custom state machine backed by our SQLite event ledger, achieving identical crash-recovery semantics for Reg E workflows.

---

## 8. langchain-ai/langgraph (Reference Only)

**Purpose:** Study stateful agent graphs and cyclical execution.

### What Idea We Use (Conceptual):
- State graph modeling where transitions depend on structured outputs and checkpoint state.

### What We Do NOT Use:
- LangChain / LangGraph libraries, bloated abstraction chains, or runtime agent dependencies.

### Why:
LangGraph introduces substantial dependency bloat and indirection. Tandem's core state machine is simple, explicit, and inspectable in vanilla Python with Pydantic and SQLAlchemy.
