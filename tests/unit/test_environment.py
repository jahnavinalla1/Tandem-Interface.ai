"""Sanity check for environment and settings."""

from tandem.config import settings


def test_settings_load():
    assert settings.tandem_env == "development"
    assert settings.core_bank_port == 8001
    assert settings.core_bank_2_port == 8002
    assert settings.processor_port == 8003
    assert settings.documents_port == 8004
    assert settings.core_bank_url == "http://127.0.0.1:8001"
