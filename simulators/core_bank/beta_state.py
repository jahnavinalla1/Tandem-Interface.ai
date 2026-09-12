"""Independent persistent state for Institution Beta."""

from __future__ import annotations

import os

from simulators.core_bank.state import CoreBankState

core_bank_beta_state = CoreBankState(
    db_path=os.environ.get("TANDEM_CORE_BETA_SIM_DB", "core_beta_simulator.db"),
    institution_id="beta",
)
