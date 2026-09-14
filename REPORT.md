## Architecture

Tandem implements one end-to-end capability: post a provisional credit in a local
synthetic banking portal. Gemini receives bounded browser observations and selects
one typed action per cycle. Playwright executes that action. A recorder preserves
observations, decisions, rationales and screenshots; a compiler produces parameterized
YAML. Replay loads YAML without constructing a model client.

The submission path is `scripts.assignment_evidence` for live discovery and
`scripts.verify_assignment` for keyless verification. Its credit pre/postchecks
operate the portal's memo inquiry screen in a separate tab of the same browser
context. They do not call the target's data APIs. Admin APIs only arrange synthetic
failure scenarios. The older multi-system effect-engine demos retain API inquiry
adapters; they are additional demonstrations, not the UI-only assignment path.

I chose a local iframe/table-based portal over a public retail demo to make failure
injection repeatable and avoid real customer data. I kept a task-specific compiler
instead of attempting universal workflow inference. Natural-language goal and target
are caller inputs, but supported inputs, routes and receipt semantics are deliberately
restricted to provisional credit. The SQLite ledger and lease broker support the
larger workflow and real ownership transfer without introducing distributed services.

## Artifact schema

The contract has independent schema and capability versions, typed input/output
schemas, ordered actions, parameter templates, locator fallbacks, scoped identity
guards, effect bounds, and pre/postcheck metadata. A source discovery run ID links
it to evidence; canonical SHA-256 detects unintended modification. Newly compiled
artifacts also contain a `derivation` block: navigation and action steps, the commit
container and run identity come from the successful trace; effect identity,
precheck/postcheck, bounds and schemas come from the reviewed capability policy;
the compiler supplies versions and the digest. This makes the artifact's origin
auditable instead of presenting policy metadata as model-discovered. A digest is
not a signature: it does not authenticate an adversarial author who can recompute it.

Fills reference declared input names rather than recorded values. Navigation uses
logical surface routes. Output properties compiled by the current compiler declare
`x-selector` extraction bindings and boolean comparison conditions. Outcome metadata
lists supported business and intervention results. Existing v1 artifacts retain
compatibility with the original credit receipt bindings. The compiler does not infer
arbitrary contracts or arbitrary route templates from a recording.

I rejected an unvalidated macro list because a caller needs identity, effect and
result semantics. The current browser executor explicitly rejects unsupported
primitives before acting; merely adding a schema enum cannot silently enable an
unimplemented operation. General per-step condition languages and automated schema
migration are deliberately outside the implemented capability.

## Determinism & error handling

Replay uses a fixed action sequence, condition-based visibility waits and ordered
fallbacks. Multiple matches are rejected rather than selecting the first. Failed
selectors record drift. Commit guards inspect the actual submitted form fields,
including member, account, case, amount, currency and institution. The UI inquiry
verifies effect identity independently; duplicate cases return `ALREADY_APPLIED`.

The caller-facing discriminated contract is success with outputs, business outcome
with code/data, or failure with classification, step, expected/observed context and
evidence reference. Internal ledger outcomes remain backward-compatible. Not-found
members are business results; compliance review requires a person; ambiguous effects
must never be blindly resubmitted. Model inference retries transient server errors
at most twice, before any browser action executes. Rate-limit/authentication errors
stop. This is separate from retries of a monetary action, which are not permitted.

The keyless verification command records success, duplicate, over-limit denial,
intervention and resumed completion. It blocks browser requests to `/api/` and asserts
zero model calls. The UI inquiry helper reads the screen with Playwright only.

## Heterogeneity & multi-tenant

Discovery includes per-frame accessibility snapshots as well as DOM attributes and
form context. Role/name targets can use Playwright's role selector engine; stable
field names/classes are fallback options. I retained these fallbacks because legacy
accessibility metadata may be incomplete. This is a web implementation, not a uniform
native-desktop element graph or a calibrated confidence-scoring model.

The `Surface` boundary isolates observation/action from business effects. A desktop
implementation would provide AX/UIA control resolution and visual verification under
the same contract. Current ambiguity handling is deterministic uniqueness rather than
a numerical confidence estimate. Tenant overlays specialize selectors and containers
without copying the effect contract; routing is configured separately. Existing Beta
demos exercise this design with curated semantic names. Automatic alignment of arbitrary
model-generated names to tenant overlays remains unimplemented. Production reuse would
key approved artifacts by vendor/version and quarantine failing canary replays.

## Escalation & handoff

The handoff coordinator releases automation ownership, records the intervention,
grants a fenced human lease, records sign-off and returns control. The browser broker
keeps the same worker-owned page and context. Integration tests verify continuity
and reject a stale automation token while the human owns the session.

Keyless evidence exercises this protocol with an explicitly scripted operator and
resumes on the same page. `--manual-handoff` instead opens a headed browser for a real
operator. Discovery writes durable intervention requests for model/action failures and
step exhaustion. With its manual option, recoverable failures pause the synchronous
loop while a person operates that same page and records action notes. Exhaustion and
ambiguous submission remain terminal; they are not silently retried. A full co-browsing
console, automatic artifact amendments from human actions, and recovery after the
browser process dies are not implemented.

## Safety

Discovery and core-bank replay share destination/method admission and inspect the
actual control's form action or link target. A commit disguised as CLICK is denied.
Both paths apply monetary policy and submitted-field identity checks. Navigation
interception rejects off-allowlist requests exposed by Playwright routing. Redirect
chains and service workers need additional transport enforcement before adapting
this policy to untrusted external applications; the local portal uses neither. Irreversible
synthetic credit is permitted only within the declared policy bounds; this is the
chosen alternative to requiring approval for every sandbox credit. Compliance
sign-off belongs to the human path. Admin mutations require a bearer token.

Before provider/evidence boundaries, credential keys and configured secrets are
redacted, along with common email, SSN and card-number patterns. Persisted evidence
also pseudonymizes the known member and account identifiers. Screenshots mask those
identifiers, password/email/card fields, marked sensitive regions and matching text
elements. This is defense in depth, not a claim that regexes detect all PII. Real institutional
deployments need application-specific region inventories, data classification and
retention policies. This submission uses only synthetic fixtures; no real customer
records should be placed in the simulator. `.env` stays outside Git.

## Cuts

A genuine Gemini discovery and compiled-artifact replay are retained in
`evidence/20260913T161444Z/`. The canonical artifact has since been recompiled from
that original trace with explicit field provenance. Additional UI-only verification
and handoff bundles live under `evidence/verification/`; their manifests identify
exactly which artifact ran. The latest complete bundle is
`evidence/verification/20260914T024243Z/`.
Debugging attempts are omitted from the submission; only the successful discovery
and the current complete verification bundle are retained.

The deliberate boundary is one concrete web capability with real failure handling,
not arbitrary task compilation, desktop automation, production financial compliance,
or multi-node orchestration. I prioritized shared safety checks, UI-only verification,
explicit failure results and real ownership transfer over those extensions. A scripted
operator demonstration is labelled as such; it is not claimed to be a recording of a
human user. The repository is public; sending it to the evaluator remains a separate
submission step.
