"""Hard process-death injection used only by crash-consistency tests.

``TANDEM_CRASH_POINT`` names the durable-write boundary at which the worker
must die. Boundaries inside a capability execution are additionally scoped by
``TANDEM_CRASH_CAPABILITY`` so the same boundary name (for example
``G_AFTER_TARGET_ACCEPTS``) can be exercised for every COMMIT capability the
procedure runs. Boundaries that belong to the orchestrator rather than to one
capability pass no capability and match on the point name alone.
"""

import os


def maybe_crash(point: str, capability_id: str | None = None) -> None:
    """Terminate immediately when the configured crash point is reached."""
    if os.environ.get("TANDEM_CRASH_POINT") != point:
        return
    scope = os.environ.get("TANDEM_CRASH_CAPABILITY")
    if scope and capability_id is not None and scope != capability_id:
        return
    os._exit(86)
