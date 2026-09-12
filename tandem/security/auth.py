"""Shared bearer-token authentication for administrative and operator mutation routes.

Root cause (H-10): every simulator's `/api/reset` and `/api/set_*` failure-injection
switches, plus the Tandem operator console's lease-claim/release and browser-session
action routes, previously had zero authentication, and the launcher bound every service
to `0.0.0.0` -- so any host that could reach the machine could reset ledger state, flip
failure switches, or seize a case lease.

`require_admin_token` is one FastAPI dependency used by every mutation route:

- Programmatic callers (tests, curl, the discovery/replay engine) send
  ``Authorization: Bearer <TANDEM_ADMIN_TOKEN>``.
- The operator console's server-rendered HTML forms (`claim_lease`, `release_lease`)
  cannot set a custom header, so they carry the same token as a hidden `admin_token`
  form field instead. Because a cross-origin page cannot read the token out of the
  victim's same-origin dashboard HTML, it cannot forge a valid form submission either
  -- this doubles as the console's CSRF defense for those two routes.

The default token is a clearly-labelled local-development value (see
`tandem.config.Settings.tandem_admin_token`); any non-development deployment must
override it via the `TANDEM_ADMIN_TOKEN` environment variable.
"""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import HTTPException, Request

from tandem.config import settings


async def require_admin_token(request: Request) -> None:
    """Reject the request unless it carries the configured admin bearer token."""
    provided = _token_from_header(request)
    if provided is None:
        provided = await _token_from_form(request)

    if provided is None or not hmac.compare_digest(provided, settings.tandem_admin_token):
        raise HTTPException(status_code=401, detail="Missing or invalid admin token")


def _token_from_header(request: Request) -> Optional[str]:
    header = request.headers.get("authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer ") :]
    return None


async def _token_from_form(request: Request) -> Optional[str]:
    content_type = request.headers.get("content-type", "")
    if not (
        content_type.startswith("application/x-www-form-urlencoded")
        or content_type.startswith("multipart/form-data")
    ):
        return None
    form = await request.form()
    value = form.get("admin_token")
    return str(value) if value is not None else None
