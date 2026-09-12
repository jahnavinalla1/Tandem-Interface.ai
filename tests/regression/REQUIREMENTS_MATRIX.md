# Phase 1 Regression Matrix

This matrix binds each minimum remediation requirement to an executable test. Existing tests are retained where they already exercise the required behavior; new tests strengthen missing adversarial boundaries. Tests marked as audit tests are intentionally stored separately and are run explicitly in CI.

| # | Required behavior | Regression evidence |
|---:|---|---|
| 1 | Real, provider-directed discovery | `tests/regression/test_remediation_contract.py::test_discovery_contract_requires_real_provider_and_durable_evidence` |
| 2 | Concurrent duplicate COMMIT | `tests/regression/test_process_safety.py::test_two_processes_produce_one_target_effect_repeated` plus atomic admission and preserved audit cases |
| 3 | Unavailable precheck | `audit_tests/test_adversarial.py::test_precheck_outage_is_not_treated_as_effect_absent` |
| 4 | Unavailable postcheck | `tests/regression/test_remediation_contract.py::test_postcheck_outage_is_explicit_and_never_success` |
| 5 | Missing guard evidence | `audit_tests/test_adversarial.py::test_guard_fails_closed_when_identity_evidence_is_missing` |
| 6 | Wrong case binding | `audit_tests/test_adversarial.py::test_wrong_case_in_commit_container_is_blocked` |
| 7 | Submitted value differs from displayed value | `audit_tests/test_adversarial.py::test_submitted_amount_cannot_diverge_after_visible_guard` |
| 8 | Wrong member | `tests/integration/test_effect_protocol.py::test_control_scoped_guard_detects_transposed_member_in_container` |
| 9 | Wrong account | `tests/regression/test_remediation_contract.py::test_guard_contract_requires_account_binding` |
| 10 | Wrong currency | `tests/regression/test_remediation_contract.py::test_guard_contract_requires_currency_binding` |
| 11 | Process death around COMMIT | `tests/regression/test_process_safety.py::test_obligation_is_durable_before_effect_claim` and `tests/regression/test_crash_matrix.py` (17 rows across core, processor, and notice COMMITs) |
| 12 | Process death around deadline creation | `tests/regression/test_process_safety.py::test_obligation_is_durable_before_effect_claim` and the Phase 13 crash matrix |
| 13 | Target service restart | `audit_tests/test_adversarial.py::test_resume_reconciles_external_state_after_simulator_restart` plus durable-store tests below |
| 14 | Real ambiguous post-submit result | `tests/regression/test_remediation_contract.py::test_after_submit_unknown_is_not_generic_hard_failure` |
| 15 | Human lease overwrite | `audit_tests/test_adversarial.py::test_active_human_lease_cannot_be_overwritten_by_automation` |
| 16 | Simultaneous workers | `tests/regression/test_process_safety.py::test_two_processes_produce_one_target_effect_repeated` |
| 17 | Simultaneous human claims | `tests/regression/test_process_safety.py::test_atomic_human_lease_across_processes` |
| 18 | Stale lease | `tests/regression/test_process_safety.py::test_stale_lease_recovery_fences_old_owner` |
| 19 | Artifact tampering | Audit selector tamper plus URL/effect/bound cases in `test_remediation_contract.py` |
| 20 | Unsupported artifact version | `tests/regression/test_remediation_contract.py::test_unsupported_artifact_schema_version_is_rejected` |
| 21 | Invalid COMMIT artifact | Audit empty-COMMIT test plus `test_commit_contract_requires_structural_guard_and_reconciliation` |
| 22 | Second-institution reuse | `tests/regression/test_remediation_contract.py::test_institution_routing_is_not_embedded_in_immutable_artifact` and Phase 25 E2E |
| 23 | Zero-model replay | `tests/e2e/test_compiled_artifact_replay.py::test_discovery_compilation_and_zero_llm_replay` and Phase 22 provider-network block |
| 24 | Processor duplicate effect | `tests/regression/test_target_persistence.py::test_processor_effect_is_durable_and_idempotent` plus `tests/regression/test_effect_protocol_unification.py` (serial duplicate, two-process race, crash rows P-R) |
| 25 | Notice duplicate effect | `tests/regression/test_target_persistence.py::test_notice_effect_is_durable_and_idempotent` plus `tests/regression/test_effect_protocol_unification.py` (serial duplicate, confirmed-absent retry, crash rows L-N) |
| 26 | NaN / Infinity amount | Audit NaN test plus finite-value matrix in `test_remediation_contract.py` |
| 27 | Deadline month/year/weekend/holiday behavior | `tests/regression/test_deadline_rules.py` |
| 28 | Wheel install | `tests/regression/test_build_contract.py::test_wheel_contains_runtime_capabilities_and_console_entrypoint` plus clean-room smoke test |
| 29 | Clean README setup | `tests/regression/test_build_contract.py::test_readme_uses_reproducible_dev_sync` plus clean-room execution |
| 30 | CI execution | `tests/regression/test_build_contract.py::test_ci_gates_full_verification_surface` plus clean-room workflow-equivalent run |
