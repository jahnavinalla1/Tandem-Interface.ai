# Live run status — 2026-09-13 UTC

Status: successful discovery and both replays verified on 2026-09-13.

## Successful run

The bundle in [`20260913T161444Z/`](20260913T161444Z/) contains:
- Genuine Gemini 3.6 Flash discovery: 9 decision cycles, 8 executed browser actions,
  10 model requests including inference retry, receipt `MC-7395`.
- Compiled `capability.yaml`, bound to discovery run
  `d55a64c6-8219-4801-a64c-42929635f3e8`.
- New-case replay: `COMPLETED`, simulated $150 credit, receipt `MC-7083`, zero model calls.
- Injected interstitial replay: `NEEDS_HUMAN` / `COMPLIANCE_INTERSTITIAL`,
  stopped before submission, no simulated money movement, zero model calls.
- Observations, screenshots, structured traces, replay logs and manifest.

Canonical artifact SHA-256:
`9db18401bdf8399af79cf7be4985673f3e224d6c1551a5c8c04abdea2e05b8fc`.

Artifact hash and discovery lineage were verified, both replay screenshots inspected,
and structured evidence checked for the configured API key (none found).
`capabilities/compiled/demo_post_provisional_credit.yaml` is now an exact copy of
this generated artifact, replacing the previous hand-authored placeholder.
All data and credits are synthetic local simulator fixtures.

## Earlier attempts (retained for debugging)

This is an operator summary of observed terminal results, not a generated success log.

The configured discovery provider authenticated successfully. An unavailable model
version was replaced with the supported model recorded in the successful trace.

Actual model-driven attempts:

| Folder | Observed result |
| --- | --- |
| `20260913T040127Z` | Navigation recorded; next fill timed out because observations omitted the control's iframe context. |
| `20260913T040259Z` | Navigation and failed fill recorded; model used HTML name `q` instead of declared input `member_id`. |
| `20260913T040332Z` | Six successful actions, through filling the credit amount; Gemini HTTP 500 stopped the next decision. |
| `20260913T040429Z` | Navigation, member search, and credit entry reached; Gemini HTTP 429 stopped the run. |

Trace files, observations and screenshots in those folders were written by the
actual discovery recorder. Empty folders from failures before the first decision
contain no useful evidence. No successful discovery receipt, compiled artifact,
replay result or success manifest was produced by these attempts.

One earlier attempt stopped at the provider's request limit. The runner stopped before
another browser action and did not switch providers. The successful trace was recorded
later and remains the canonical discovery evidence.

Fixes validated after live testing:
- Browser observations identify each control's iframe and expose name/class/href
  and form context to support stable targeting.
- The Gemini output schema restricts `input_name` to declared caller parameters.
- Failed browser decisions retain an error type, observation and screenshot.
- Transient 500/502/503/504 inference errors get at most two retries; no browser
  action executes until a complete validated decision returns. Quota errors stop.
- Targeted provider/compiler/browser/replay suite: 26 passed. Lint/types passed.

## Completion verification

[`verification/20260913T214456Z/`](verification/20260913T214456Z/manifest.json)
replays the genuine saved artifact using UI-only pre/postchecks and no model calls.
All five scenarios passed: completion, duplicate business outcome, policy denial,
compliance intervention, and same-page resumed completion. `handoff.json` records
real ownership transfer and stale-token rejection with an explicitly scripted
operator. A real person can perform sign-off using `--manual-handoff`.
Screenshots and structured results are included; the resumed receipt was visually
inspected. The earlier `verification/20260913T165925Z/` attempt failed due to browser
context creation and has no success manifest. That issue was fixed before both
subsequent successful verification bundles.

The original discovery bundle remains unchanged as historical evidence. New
accessibility observations and output binding metadata have automated test coverage;
the original saved artifact predates those additions. Nothing has been published or emailed.

Final completion checks: 208 tests passed; three dependency deprecation warnings.
Lint, selected-module type checks, evidence lineage and configured-secret scan passed.
