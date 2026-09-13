"""Redact credential and common PII patterns before provider/evidence boundaries."""

import re
from typing import Any

from playwright.sync_api import Page

from tandem.config import settings

_PATTERNS = [
    re.compile(r'\b\d{3}-\d{2}-\d{4}\b'),
    re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'),
    re.compile(r'\b(?:\d[ -]?){13,19}\b'),
    re.compile(r'(?i)\b(?:bearer\s+)[A-Za-z0-9._-]+'),
]


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


def screenshot(page: Page) -> bytes:
    masks = []
    pattern = re.compile('|'.join(p.pattern for p in _PATTERNS[:3]))
    for frame in page.frames:
        masks.append(frame.locator('[data-sensitive], [data-pii], input[type="password"], '
                                   'input[type="email"], [autocomplete="cc-number"], '
                                   '[autocomplete="current-password"]'))
        masks.append(frame.locator('span, strong, td, p, label, a').filter(has_text=pattern))
    return page.screenshot(full_page=True, mask=masks)
