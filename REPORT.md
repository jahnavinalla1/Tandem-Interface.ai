## Architecture

Tandem separates model-driven browser discovery, compilation, and deterministic
execution. The implemented target is a local synthetic core-banking portal with
frames and a multi-step provisional-credit flow. Playwright drives the UI. A typed
provider boundary (Gemini by default, with optional OpenAI support) sends bounded observations to the model and accepts one validated
action per cycle. The loop records decisions, rationales, actions and screenshots.
`discover(goal, target, inputs)` accepts the caller's objective; input validation,
receipt recognition and the compiler remain specific to provisional credit.

The compiler converts actual recorded actions into parameterized YAML. Replay loads
that artifact without asking a model for decisions. The larger workflow adds a
SQLite effect ledger, prechecks, postchecks and ownership leases. This separation
makes money-moving uncertainty explicit, at the cost of more machinery than a
single-flow recorder requires. Simulator admin APIs control test fixtures; some
postchecks use simulator inquiry APIs. Thus the current end-to-end system is not
fully UI-only, an important limitation for the assignment's no-API environment.

## Artifact schema

A capability has an ID, version, typed input/output schemas, ordered actions,
semantic control names, ordered locator candidates, optional frame selectors,
parameter templates and a scoped commit guard. Effect metadata declares identity,
monetary bounds, duplicate detection and reconciliation. Receipt and postcheck
verification supply the success checkpoint. The compiler records the source
run ID and creation time; a canonical SHA-256 digest detects accidental edits,
not malicious replacement by someone able to recompute the digest.

Inputs separate reusable flow structure from member, account, case and amount.
The guard binds the actual submitted fields in the commit control's container,
rather than accepting matching text elsewhere on the page. The artifact can be
reviewed independently of the model transcript. Compilation is domain-specific:
it synthesizes the credit contract rather than inferring arbitrary task schemas.

## Determinism & error handling

Replay executes ordered steps with bounded waits and ordered selector fallbacks.
It records fallback drift and checks that the model-call counter has not increased.
The effect engine checks for a prior effect before submission and reconciles an
ambiguous submission rather than blindly repeating it. The low-level executor is
not a substitute for the ledger-backed effect engine when invoking repeated cases.

Results distinguish successful completion, business outcomes such as
`ALREADY_APPLIED`, recoverable/intervention states such as compliance review or
uncertain effects, and hard failures such as an entity mismatch. Structured results
carry an outcome code, message, effect state and debugging detail. Session expiry,
missing controls and guard failures stop progress. The evidence command saves a
screenshot and structured result for an injected compliance interstitial as well
as the normal replay. It reloads the exact compiled YAML and uses new case inputs.

## Heterogeneity & multi-tenant

The `Surface` boundary separates control observation and action from recorded flow
semantics. The concrete driver supports frames and selector candidates. A second
simulator skin demonstrates trusted tenant routing and locator overlays while
preserving the underlying artifact. This supports reuse but does not establish
compatibility with arbitrary vendor versions.

For legacy web, extend observation to frame paths and accessible roles with scoped
text anchors. For native desktop, implement the same boundary using accessibility
controls, with screenshot/coordinate targeting only under explicit visual checks.
Neither desktop nor general visual targeting is implemented. At scale, key approved
artifacts by vendor/product/version and bind reviewed tenant overlays separately.
Canary replays and fallback-rate monitoring should quarantine incompatible versions;
never silently let a tenant override alter effect identity or monetary policy.

## Escalation & handoff

Replay can stop on an interstitial or uncertain state and persist an intervention.
The handoff coordinator uses operator leases and fencing tokens to prevent stale
owners from acting. A browser-session broker retains a Playwright page on its owning
worker thread; the operator API can issue actions on that same session. Releasing
control and resuming must preserve the case, session and effect evidence.

The in-process compliance demo exercises clearing the interstitial and resuming.
Generic browser-session HTTP actions exist, but the specialized compliance-clear
helper is not directly exposed as an HTTP endpoint. This is a minimal operator
mechanism, not a complete co-browsing console. Discovery currently raises on its
cycle limit or execution failure; automated routing of discovery failures into the
same durable intervention mechanism remains unfinished.

## Safety

Replay policy includes effect bounds, trusted surface routing and control-scoped
identity checks; admin mutations require a bearer token. Discovery constrains the
target to the configured simulator origin and validates action shapes. Its safety
checks are less complete than replay: model action classification and selector
choice are not a security boundary, and redirects/click navigation need stronger
per-action enforcement. Use discovery only on the synthetic local portal.

Structured credential-like keys are redacted. OpenAI requests disable storage;
Gemini free-tier data may be used by Google to improve its products.
This is not comprehensive PII protection: raw page text, URLs and screenshots can
retain sensitive information. No real credentials or customer data belong in this
demo. Production would require pre-provider sanitization, screenshot masking,
retention controls, secret isolation and independent authorization before submission.

## Cuts

The required discovery/replay bundle is saved in `evidence/20260913T161444Z/`.
Gemini 3.6 Flash completed 9 decision cycles and 8 browser actions; 10 inference
requests include a retry. The compiled artifact replayed with new case inputs and
zero model calls. An injected compliance interstitial returned `NEEDS_HUMAN` before
submission. The manifest binds both replays to the discovery run and artifact hash.
Earlier failed attempts are retained; live testing exposed iframe-context and
input-name defects that were fixed and regression-tested.

Deliberate scope limits include one task-specific compiler, synthetic targets,
no desktop adapter, no production deployment and no full operator console. The evidence bundle does not include a manual takeover/resume recording. Next priorities
are discovery guard parity and failure handoff, UI-only inquiry capabilities,
strict output-schema enforcement, and stronger observation redaction. Public
repository publishing and submission email are separate user-controlled steps.
