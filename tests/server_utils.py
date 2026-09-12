"""Test-only convenience wrapper around `tandem.support.simulator_control`.

Kept as a thin re-export so existing test imports (`from tests.server_utils import
ensure_simulators_running, reset_all_simulators`) keep working; the actual
implementation lives in `tandem.support.simulator_control`, which is production-safe
(no test-module imports) so `scripts/demo.py` can use it too (H-06).
"""

from tandem.support.simulator_control import (  # noqa: F401
    ensure_simulators_running,
    is_port_open,
    reset_all_simulators,
    start_server_in_thread,
)
