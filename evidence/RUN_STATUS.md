# Submission evidence status

Two evidence bundles are retained because they prove different parts of the
assignment. All records use synthetic local simulator data.

## Provider-backed discovery

[`20260913T161444Z/`](20260913T161444Z/manifest.json) contains the successful live
discovery run:

- Gemini observed the hostile browser UI and made 9 structured decisions.
- Playwright executed 8 browser actions and reached receipt `MC-7395`.
- The saved artifact is bound to discovery run
  `d55a64c6-8219-4801-a64c-42929635f3e8`.
- Fresh-case replay completed with zero model calls.
- Injected compliance review stopped before submission with zero model calls.

The preserved historical artifact verifies against SHA-256
`9db18401bdf8399af79cf7be4985673f3e224d6c1551a5c8c04abdea2e05b8fc`.
It remains unchanged so reviewers can compare the trace with the artifact produced
at discovery time.

## Current deterministic verification

[`verification/20260914T024243Z/`](verification/20260914T024243Z/manifest.json)
replays the current canonical artifact, SHA-256
`f92adf85ccfdcc212aca16f073600410a9babcd19a36670c90c09dbe64addbf9`.
It retains the original discovery run ID and adds field-level derivation metadata.

All five checks passed with zero model calls:

1. A new case completed and produced a receipt.
2. Repeating the same case returned `ALREADY_APPLIED`.
3. An over-limit amount returned `POLICY_DENIED` before COMMIT.
4. A compliance interstitial returned `NEEDS_HUMAN` before COMMIT.
5. A fenced operator handoff resumed on the same browser page and completed.

The verification uses UI-only effect inquiries and blocks target `/api/` browser
requests. Stored JSON pseudonymizes the configured member/account identifiers and
screenshots mask those identifiers. The operator in the committed automated bundle
is explicitly labelled scripted; `--manual-handoff` provides the human-operated
alternative.

The canonical and historical artifact hashes, discovery lineage, zero-model counters,
and configured-secret scan were verified before submission. Debugging attempts and
duplicate verification bundles are intentionally excluded from the repository.
