"""Redact credential and common PII patterns before provider/evidence boundaries."""

import re
from collections.abc import Iterable
from typing import Any

from playwright.sync_api import Page

from tandem.config import settings

_PATTERNS = [
    re.compile(r'\b\d{3}-\d{2}-\d{4}\b'),
    re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'),
    re.compile(r'\b(?:\d[ -]?){13,19}\b'),
    re.compile(r'(?i)\b(?:bearer\s+)[A-Za-z0-9._-]+'),
]

_IDENTIFIER_KEYS = {
    'member_id',
    'account_id',
    'source_member_id',
    'target_member_id',
    'source_account_id',
    'target_account_id',
    'expected_entity',
    'observed_entity',
}


def mask_identifier(value: str) -> str:
    """Retain only a short suffix so evidence can be correlated safely."""
    text = str(value)
    if len(text) <= 4:
        return '*' * len(text)
    visible = min(4, len(text))
    return '*' * max(4, len(text) - visible) + text[-visible:]


def redact_text(value: str) -> str:
    for secret in (settings.gemini_api_key, settings.openai_api_key, settings.anthropic_api_key,
                   settings.tandem_admin_token):
        if secret:
            value = value.replace(secret, '[REDACTED]')
    for pattern in _PATTERNS:
        value = pattern.sub('[REDACTED]', value)
    return value


def sanitize(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {key: ('[REDACTED]' if any(part in key.lower() for part in
                                       ('password', 'secret', 'token', 'api_key', 'authorization'))
                      else sanitize(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def sanitize_evidence(value: Any, *, identifiers: Iterable[str] = ()) -> Any:
    """Sanitize persisted evidence and pseudonymize member/account identifiers."""
    known = tuple(
        sorted({str(item) for item in identifiers if str(item)}, key=len, reverse=True)
    )

    def visit(item: Any) -> Any:
        if isinstance(item, str):
            redacted = redact_text(item)
            for identifier in known:
                redacted = redacted.replace(identifier, mask_identifier(identifier))
            return redacted
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                key_lower = key.lower()
                if any(part in key_lower for part in
                       ('password', 'secret', 'token', 'api_key', 'authorization')):
                    result[key] = '[REDACTED]'
                elif key_lower in _IDENTIFIER_KEYS and isinstance(child, str):
                    result[key] = mask_identifier(child)
                else:
                    result[key] = visit(child)
            return result
        if isinstance(item, list):
            return [visit(child) for child in item]
        return item

    return visit(value)


def screenshot(page: Page, *, identifiers: Iterable[str] = ()) -> bytes:
    """Capture evidence after masking sensitive fields and known identifiers."""
    masks = []
    pattern = re.compile('|'.join(p.pattern for p in _PATTERNS[:3]))
    identifier_values = tuple(str(item) for item in identifiers if str(item))
    identifier_pattern = (
        re.compile('|'.join(re.escape(item) for item in identifier_values))
        if identifier_values else None
    )
    for frame in page.frames:
        masks.append(frame.locator('[data-sensitive], [data-pii], input[type="password"], '
                                   'input[type="email"], [autocomplete="cc-number"], '
                                   '[autocomplete="current-password"], '
                                   'input[name="member_id"], input[name="account_id"], '
                                   '.cell-member-id, .cell-account-id, .scoped-member-id, '
                                   '.scoped-account-id, .found-member-id, .found-account-id, '
                                   '#interstitial_member_id, #interstitial_account_id, '
                                   '#receipt_member_id'))
        masks.append(frame.locator('span, strong, td, p, label, a').filter(has_text=pattern))
        if identifier_pattern is not None:
            masks.append(
                frame.locator('span, strong, td, p, label, a').filter(
                    has_text=identifier_pattern
                )
            )
    return page.screenshot(full_page=True, mask=masks)
