# Assignment delivery map

This maps the assignment requirements to inspectable code and evidence. It does not
claim that every aspirational suggestion in the feedback is implemented.

| Requirement | Implementation / evidence |
| --- | --- |
| Natural-language goal and target | `scripts.assignment_evidence --goal ... --target ...`; supported task is synthetic provisional credit |
| Actual LLM-driven browser discovery | `evidence/20260913T161444Z/` has genuine Gemini observations, decisions and screenshots |
| Versioned reusable artifact | That bundle's `capability.yaml`; typed input/output schemas, guards, source run and hash |
| Replay without model calls | `make verify-assignment`; latest bundle `evidence/verification/20260913T214456Z/` records zero calls |
| Independent effect verification | `tandem/replay/ui_inquiry.py` reads the memo inquiry screen; target API requests blocked in verification |
| Business outcomes and failures | Duplicate, denied and intervention logs; discriminated result contract in `tandem/domain/result_contract.py` |
| Policy and sensitive evidence | Shared browser admission, bound-form checks and `tandem/security/evidence.py` |
| Handoff and resume | Latest bundle's `handoff.json` and resumed receipt; real leases, same page, scripted operator explicitly labelled |
| Human-operated option | `python -m scripts.verify_assignment --manual-handoff`; headed browser and return of control |
| Heterogeneous / tenant design | Surface boundary and overlays; REPORT explains native desktop extension and current limits |
| Setup and required report | README and REPORT with all seven required headings |

Run the keyless verification after the README setup:

```sh
make verify-assignment
```

The discovery evidence uses synthetic fixtures only. No API key is needed to review
or replay it. `.env` and operator databases are ignored by Git.

Scope limits: native desktop execution, universal task compilation, automatic tenant
semantic alignment and a full remote co-browsing UI are not implemented. The report
explains these cuts. The recorded operator is scripted; it is not presented as a
recording of a person. Public repository publication and submission are not performed.

Final verification: 208 tests passed (three dependency deprecation warnings); lint,
selected-module type checks and whitespace checks passed. Latest end-to-end demo
passed all five scenarios. No new Gemini calls were needed for this verification.
