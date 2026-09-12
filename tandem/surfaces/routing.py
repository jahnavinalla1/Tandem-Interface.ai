"""Trusted runtime routing kept outside immutable capability artifacts."""

from tandem.config import settings


def normalize_institution_id(institution_id: object) -> str:
    value = str(institution_id or "alpha").lower()
    aliases = {
        "core_bank_alpha": "alpha",
        "core_bank_beta": "beta",
    }
    return aliases.get(value, value)


def core_bank_url_for(institution_id: object) -> str:
    """Resolve an institution to its configured external core service."""

    normalized = normalize_institution_id(institution_id)
    routes = {
        "alpha": settings.core_bank_url,
        "beta": settings.core_bank_2_url,
    }
    if normalized not in routes:
        raise ValueError(f"Unsupported core-bank institution '{normalized}'")
    return routes[normalized]
