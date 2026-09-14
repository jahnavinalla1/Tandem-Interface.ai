# Assessment requirement status

This file maps 27 assessment requirements to concrete implementation or an explicit
scope boundary. It is a review aid for the tests and evidence.

| # | Requested improvement | Status and proof |
|---:|---|---|
| 1 | Real observe-decide-act discovery | Implemented by `DiscoveryAgent`; the successful provider-backed trace is in `evidence/20260913T161444Z/`. |
| 2 | Artifact derived from the run with provenance | Implemented. The compiler consumes executed trace events and records `derivation` for discovery, policy-profile and compiler fields. |
| 3 | Atomic effect claim | Implemented by the durable effect protocol and ledger identity key. |
| 4 | Fail-closed prechecks | Implemented. Inquiry failure stops the COMMIT path instead of treating it as absent. |
| 5 | Verify exact submitted values | Implemented by the scoped bound-form guard for institution, member, account, case, amount and currency. |
| 6 | Post-commit verification | Implemented with independent memo inquiry and receipt binding. |
| 7 | Reconciliation | Implemented. Ambiguous COMMIT outcomes are confirmed by inquiry or become `UNCERTAIN_EFFECT`. |
| 8 | Crash-safe ordering | Implemented and covered by crash-injection integration tests. |
| 9 | Append-only ledger | Implemented with SQLite triggers, hash chaining and integrity checks. |
| 10 | Durable simulator state | Implemented with SQLite-backed target services. |
| 11 | Same protocol for every COMMIT | Implemented through the shared effect engine; adversarial tests cover bypass attempts. |
| 12 | Real handoff | Implemented with the same brokered browser page; manual and explicitly scripted operator paths are available. |
| 13 | Leases and fencing | Implemented with ownership transfer, expiry and stale-token rejection. |
| 14 | Destination allowlist | Implemented for browser navigation, actions and requests in the supported local portal. |
| 15 | Secrets and PII in evidence | Improved. Credentials are redacted; known member/account identifiers are pseudonymized in JSON and masked in screenshots. |
| 16 | Exact money arithmetic | Implemented with `Decimal` in policy/effect code and verification/demo inputs. |
| 17 | Simpler scope | The public CLI now presents five assessment scenarios; broader behavior stays in tests. |
| 18 | Genuine second institution or honest downgrade | Institution Beta is an independent service with a structural overlay; verified in integration tests and described without claiming arbitrary portability. |
| 19 | Hostile UI | Implemented with iframes, dynamic IDs, ambiguity, interstitials and lost-response injection. |
| 20 | Implement or remove exposed actions | Completed. The artifact enum now exposes only executor-supported actions. |
| 21 | Evidence | Discovery and current verification bundles include structured events, screenshots, manifests and hashes. |
| 22 | Integrity | Artifact SHA-256, evidence lineage and ledger hash-chain verification are implemented. |
| 23 | Clear outcomes | Success, business outcome, intervention and technical failure are discriminated in the result contract. |
| 24 | Five decisive demos | Implemented: discovery, fresh replay, duplicate replay, wrong member/amount, and uncertain effect. |
| 25 | Setup, CI and Docker | README setup is reproducible; CI runs lint, types, tests, package build, wheel smoke test and a real container build. |
| 26 | Accurate claims | README, REPORT and evidence status distinguish measured behavior, historical artifacts and current limitations. |
| 27 | Simple pitch | README leads with: “Discover once, replay safely—even when the action moves money and the outcome is uncertain.” |

Current boundaries remain explicit: the compiler supports one provisional-credit web
capability; native desktop execution, automatic arbitrary-task compilation and a
production multi-node control plane are not implemented.
