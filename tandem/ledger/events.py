"""Canonical serialization and verification values for procedure events."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class ChainVerification:
    """Result of verifying one case's immutable event stream."""

    valid: bool
    broken_sequence: int | None = None
    message: str = ""


def canonical_payload(payload: dict[str, Any] | None) -> str | None:
    """Serialize an event payload deterministically for storage and hashing."""
    if payload is None:
        return None
    return json.dumps(payload, default=str, sort_keys=True, separators=(",", ":"))


def compute_event_hash(
    *,
    event_id: str,
    case_id: str,
    sequence: int,
    event_type: str,
    step_name: str,
    actor: str,
    payload: str | None,
    created_at: str,
    previous_event_hash: str,
) -> str:
    """Hash every immutable event field using canonical JSON."""
    envelope = {
        "actor": actor,
        "case_id": case_id,
        "created_at": created_at,
        "event_id": event_id,
        "event_type": event_type,
        "payload": payload,
        "previous_event_hash": previous_event_hash,
        "sequence": sequence,
        "step_name": step_name,
    }
    encoded = json.dumps(
        envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
