"""Seed script to initialize simulators and ledger with fresh demo data."""

import httpx

from simulators.core_bank.state import core_bank_state
from simulators.documents.state import document_state
from simulators.processor.state import processor_state
from tandem.config import settings
from tandem.ledger.database import _default_engine, init_db


def seed_all():
    print("[*] Initializing procedure ledger tables...")
    init_db(_default_engine)

    print("[*] Seeding simulator in-memory state...")
    core_bank_state.seed()
    processor_state.reset()
    document_state.reset()

    print("[*] Resetting running simulator HTTP endpoints if online...")
    admin_headers = {"Authorization": f"Bearer {settings.tandem_admin_token}"}
    for name, url in [
        ("Core Bank", settings.core_bank_url),
        ("Core Bank Beta", settings.core_bank_2_url),
        ("Card Processor", settings.processor_url),
        ("Notice Documents", settings.documents_url),
    ]:
        try:
            resp = httpx.post(f"{url}/api/reset", headers=admin_headers, timeout=1.0)
            print(f"  [+] {name} ({url}) reset: {resp.status_code}")
        except Exception:
            print(f"  [-] {name} ({url}) offline or not yet started (skipped HTTP reset)")

    print("[+] Seed completed successfully!")


if __name__ == "__main__":
    seed_all()
