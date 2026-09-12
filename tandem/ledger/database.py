"""SQLite database engine, schema migration, and append-only protections."""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from tandem.config import settings
from tandem.ledger.events import GENESIS_HASH, compute_event_hash
from tandem.ledger.models import Base


def get_engine(db_path: str = "tandem_ledger.db"):
    """Create SQLite engine configured with crash-safe WAL mode."""
    # Ensure directory exists if path includes subdirectories
    path_obj = Path(db_path)
    if path_obj.parent and str(path_obj.parent) != ".":
        path_obj.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        connect_args={"timeout": 30.0},
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    return engine


def init_db(engine) -> None:
    """Create all ledger tables if they do not already exist."""
    Base.metadata.create_all(bind=engine)
    _migrate_case_leases(engine)
    _migrate_event_stream(engine)


def _migrate_case_leases(engine) -> None:
    """Upgrade overwriteable leases to expiring, versioned fencing records."""

    with engine.begin() as connection:
        columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(case_leases)")
        }
        additions = {
            "resource_id": "TEXT",
            "owner_type": "TEXT",
            "fencing_token": "INTEGER DEFAULT 1",
            "version": "INTEGER DEFAULT 1",
            "heartbeat_at": "DATETIME",
            "expires_at": "DATETIME",
        }
        for name, column_type in additions.items():
            if name not in columns:
                connection.exec_driver_sql(
                    f"ALTER TABLE case_leases ADD COLUMN {name} {column_type}"
                )
        connection.exec_driver_sql(
            """UPDATE case_leases
                  SET resource_id=COALESCE(resource_id, case_id),
                      owner_type=COALESCE(owner_type,
                          CASE WHEN owner LIKE 'AUTOMATION%' THEN 'AUTOMATION' ELSE 'HUMAN' END),
                      fencing_token=COALESCE(fencing_token, 1),
                      version=COALESCE(version, 1),
                      heartbeat_at=COALESCE(heartbeat_at, acquired_at),
                      expires_at=COALESCE(expires_at, datetime(acquired_at, '+5 minutes'))"""
        )


def _migrate_event_stream(engine) -> None:
    """Upgrade pre-chain SQLite ledgers and enforce append-only audit facts."""
    with engine.begin() as connection:
        columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(procedure_events)")
        }
        additions = {
            "event_id": "TEXT",
            "sequence": "INTEGER",
            "created_at": "TEXT",
            "previous_event_hash": "TEXT",
            "event_hash": "TEXT",
        }
        for name, column_type in additions.items():
            if name not in columns:
                connection.exec_driver_sql(
                    f"ALTER TABLE procedure_events ADD COLUMN {name} {column_type}"
                )

        rows = connection.exec_driver_sql(
            """SELECT id, case_id, event_type, step_name, actor, payload, timestamp,
                      event_id, sequence, created_at, previous_event_hash, event_hash
                 FROM procedure_events ORDER BY case_id, id"""
        ).mappings()
        last_by_case: dict[str, tuple[int, str]] = {}
        for row in rows:
            previous_sequence, previous_hash = last_by_case.get(
                row["case_id"], (0, GENESIS_HASH)
            )
            sequence = int(row["sequence"] or previous_sequence + 1)
            created_at = row["created_at"] or _as_utc_iso(row["timestamp"])
            event_id = row["event_id"] or str(uuid4())
            prior = row["previous_event_hash"] or previous_hash
            event_hash = row["event_hash"] or compute_event_hash(
                event_id=event_id,
                case_id=row["case_id"],
                sequence=sequence,
                event_type=row["event_type"],
                step_name=row["step_name"],
                actor=row["actor"],
                payload=row["payload"],
                created_at=created_at,
                previous_event_hash=prior,
            )
            if not all(
                (
                    row["event_id"],
                    row["sequence"],
                    row["created_at"],
                    row["previous_event_hash"],
                    row["event_hash"],
                )
            ):
                connection.exec_driver_sql(
                    """UPDATE procedure_events
                          SET event_id=?, sequence=?, created_at=?,
                              previous_event_hash=?, event_hash=?
                        WHERE id=?""",
                    (event_id, sequence, created_at, prior, event_hash, row["id"]),
                )
            last_by_case[row["case_id"]] = (sequence, event_hash)

        for case_id, (sequence, event_hash) in last_by_case.items():
            connection.exec_driver_sql(
                """INSERT INTO event_stream_heads(case_id, last_sequence, last_event_hash)
                   VALUES (?, ?, ?)
                   ON CONFLICT(case_id) DO UPDATE SET
                       last_sequence=excluded.last_sequence,
                       last_event_hash=excluded.last_event_hash""",
                (case_id, sequence, event_hash),
            )

        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_procedure_events_stream_sequence "
            "ON procedure_events(case_id, sequence)"
        )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_procedure_events_event_id "
            "ON procedure_events(event_id)"
        )
        connection.exec_driver_sql(
            """CREATE TRIGGER IF NOT EXISTS procedure_events_append_only_update
               BEFORE UPDATE ON procedure_events
               BEGIN
                   SELECT RAISE(ABORT, 'procedure_events are append-only');
               END"""
        )
        connection.exec_driver_sql(
            """CREATE TRIGGER IF NOT EXISTS procedure_events_append_only_delete
               BEFORE DELETE ON procedure_events
               BEGIN
                   SELECT RAISE(ABORT, 'procedure_events are append-only');
               END"""
        )


def _as_utc_iso(value: object) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace(" ", "T"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def get_session_factory(engine) -> sessionmaker[Session]:
    """Return configured session factory."""
    return sessionmaker(bind=engine, expire_on_commit=False)


# Default application-wide engine and sessionmaker
_default_engine = get_engine(settings.tandem_db_path)
init_db(_default_engine)
SessionLocal = get_session_factory(_default_engine)


def get_db():
    """FastAPI dependency yielding database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
