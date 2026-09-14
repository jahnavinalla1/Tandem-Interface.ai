# Assignment evidence

The successful bundle is [20260913T161444Z](20260913T161444Z/manifest.json).
It contains a genuine Gemini 3.6 Flash discovery, the compiled artifact, successful
new-case replay and an injected-interstitial replay. Both replays used zero model
calls. See [RUN_STATUS.md](RUN_STATUS.md) for the exact verified results.

The latest keyless verification of the current canonical artifact is
[verification/20260914T024243Z](verification/20260914T024243Z/manifest.json). It
records five passing outcomes, zero model calls, real lease transfer, and masked
member/account evidence.

To produce another bundle, configure the discovery provider credentials in the
ignored `.env` file and run from the repository root:

```sh
.venv/bin/python -m scripts.assignment_evidence \
  --goal "Find the supplied member, post the supplied provisional credit once, and verify the receipt" \
  --target http://127.0.0.1:8001
```

The command starts local simulator services if needed and uses synthetic fixtures.
It posts simulated credits with unique case IDs; it does not reset existing databases.
Use a fresh local simulator instance with default modes. It temporarily enables a
compliance interstitial for the exceptional replay and disables it afterward.

A timestamped folder contains the real discovery trace, per-cycle observations and
screenshots, `capability.yaml`, successful and exceptional replay JSON and screenshots,
and a manifest binding both replays to the discovery run and artifact hash. Replay
loads that exact saved YAML, uses a new case ID, and verifies zero model calls.
A manifest is written only after the expected outcomes pass. Failed runs retain
partial evidence; do not describe them as successful submissions.

Review generated files before making them public. The recorder masks configured
secrets, common sensitive-data patterns, and the known synthetic member/account
identifiers, but the simulator should still contain only synthetic records.
