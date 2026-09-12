"""Pytest configuration and global fixtures for Tandem test suite."""
import pytest

from tests.server_utils import ensure_simulators_running, reset_all_simulators


@pytest.fixture(scope="session", autouse=True)
def init_test_servers():
    """Ensure all simulator servers are running for the test session."""
    ensure_simulators_running()


@pytest.fixture(autouse=True)
def clean_simulator_states():
    """Reset all simulators to pristine initial seed state before and after every test."""
    reset_all_simulators()
    yield
    reset_all_simulators()
