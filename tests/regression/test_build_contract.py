"""Static build and CI contracts backed by later clean-room execution."""

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]


def test_readme_uses_reproducible_dev_sync() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "uv sync --extra dev --frozen" in readme


def test_ci_gates_full_verification_surface() -> None:
    workflows = list((ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows
    combined = "\n".join(path.read_text(encoding="utf-8") for path in workflows)
    required = [
        "uv sync --extra dev --frozen",
        "ruff check .",
        "mypy tandem simulators",
        "pytest tests",
        "pytest audit_tests",
        "uv build",
        "playwright install",
    ]
    assert all(command in combined for command in required)
    assert "continue-on-error" not in combined


def test_wheel_contains_runtime_capabilities_and_console_entrypoint() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[project.scripts]" in pyproject
    assert "capabilities" in pyproject


def test_docker_compose_uses_durable_directory_or_named_volumes() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    volumes = compose.get("volumes", {})
    assert volumes
    service_mounts = [
        mount
        for service in compose["services"].values()
        for mount in service.get("volumes", [])
    ]
    assert all("tandem_ledger.db:tandem_ledger.db" not in str(mount) for mount in service_mounts)

