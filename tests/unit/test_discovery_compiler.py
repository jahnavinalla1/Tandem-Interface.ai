"""Unit tests for Discovery Trace Recorder and Capability Compiler."""

from pathlib import Path

from tandem.discovery.compiler import CapabilityCompiler
from tandem.discovery.recorder import TraceRecorder
from tandem.domain.capability import EffectClass, load_capability_from_yaml


def test_trace_recorder_records_actions_and_finalizes():
    recorder = TraceRecorder(
        capability_id="core.post_provisional_credit",
        goal="Discover provisional credit steps",
        system="core_bank",
    )

    recorder.record_navigate("http://127.0.0.1:8001/")
    recorder.record_fill(
        selector="input[name='q']",
        value="8830142",
        input_name="member_id",
        frame_selector="#core_workspace_frame",
        locator_candidates=["input[name='q']", "#search_input"],
    )
    recorder.record_click(
        selector="button[type='submit']",
        frame_selector="#core_workspace_frame",
        locator_candidates=["button[type='submit']", "#search_btn"],
    )

    trace = recorder.finalize(discovered_memo="MC-7711", money_moved=True)

    assert trace.capability_id == "core.post_provisional_credit"
    assert trace.system == "core_bank"
    assert len(trace.actions) == 3
    assert trace.actions[0].action == "NAVIGATE"
    assert trace.actions[1].action == "FILL"
    assert trace.actions[1].input_name == "member_id"
    assert trace.actions[2].action == "CLICK"
    assert trace.discovered_memo == "MC-7711"
    assert trace.money_moved is True


def test_capability_compiler_generates_valid_yaml_and_hash(tmp_path: Path):
    recorder = TraceRecorder(
        capability_id="core.post_provisional_credit",
        goal="Post provisional credit after verification",
        system="core_bank",
    )
    recorder.record_navigate("http://127.0.0.1:8001/")
    recorder.record_fill(
        selector="input[name='q']",
        value="8830142",
        input_name="member_id",
        frame_selector="#core_workspace_frame",
    )
    recorder.record_fill(
        selector="input[name='case_id']",
        value="D-9001",
        input_name="case_id",
        frame_selector="#core_workspace_frame",
    )
    recorder.record_fill(
        selector="input[name='amount']",
        value="250.00",
        input_name="amount",
        frame_selector="#core_workspace_frame",
    )
    recorder.record_click(
        selector="button.btn-commit-final",
        frame_selector="#core_workspace_frame",
        container_selector="#credit_action_container, .confirm-panel",
        is_mutating=True,
        guard_ref="primary_commit_guard",
    )

    trace = recorder.finalize(discovered_memo="MC-8123", money_moved=True)

    compiler = CapabilityCompiler(output_dir=str(tmp_path / "compiled"))
    capability, output_path = compiler.compile(trace, target_filename="test_cap.yaml")

    assert output_path.exists()
    assert capability.artifact_hash is not None
    assert len(capability.artifact_hash) == 64  # Valid SHA-256 hex string

    # Load back using domain loader to verify YAML roundtrip and validation
    loaded = load_capability_from_yaml(str(output_path))
    assert loaded.id == "core.post_provisional_credit"
    assert loaded.effect.effect_class == EffectClass.COMMIT
    assert loaded.effect.bounds.max_amount == 500.00
    assert loaded.scoped_guard.container_selector == "#credit_action_container, .confirm-panel"
    assert len(loaded.steps) == 5
    assert loaded.steps[1].input_value_template == "{{input.member_id}}"
    assert loaded.steps[2].input_value_template == "{{input.case_id}}"
    assert loaded.steps[3].input_value_template == "{{input.amount}}"
