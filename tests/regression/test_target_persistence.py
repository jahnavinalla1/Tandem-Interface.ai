"""Durability and idempotency contracts for external-system simulators."""

import multiprocessing
from decimal import Decimal
from pathlib import Path

from simulators.core_bank.state import CoreBankState
from simulators.documents.state import DocumentSystemState
from simulators.processor.state import ProcessorState


def _write_core_effect(db_path: str) -> None:
    state = CoreBankState(db_path=db_path, institution_id="alpha")
    state.seed()
    state.post_credit("D-PROCESS-CORE", "8830142", Decimal("340.00"))


def _write_processor_effect(db_path: str) -> None:
    ProcessorState(db_path=db_path).file_chargeback(
        "D-PROCESS-CB", "4112", Decimal("340.00")
    )


def _write_notice_effect(db_path: str) -> None:
    DocumentSystemState(db_path=db_path).send_notice(
        "D-PROCESS-NOTICE",
        "8830142",
        "REG_E_PROVISIONAL_CREDIT_DISCLOSURE",
        Decimal("340.00"),
        "2026-09-15T17:00:00-04:00",
    )


def test_core_effect_survives_restart_and_keeps_history(tmp_path: Path) -> None:
    db_path = str(tmp_path / "core.db")
    first = CoreBankState(db_path=db_path, institution_id="alpha")
    first.seed()
    credit = first.post_credit(
        "D-DURABLE-CORE",
        "8830142",
        Decimal("340.00"),
        currency="USD",
        business_reference="D-DURABLE-CORE",
    )
    first.close()

    restarted = CoreBankState(db_path=db_path, institution_id="alpha")
    found = restarted.find_credit_by_case("D-DURABLE-CORE")
    assert found == credit
    assert restarted.effect_count("D-DURABLE-CORE") == 1
    assert restarted.get_member("8830142").balance == Decimal("1580.50")


def test_processor_effect_is_durable_and_idempotent(tmp_path: Path) -> None:
    db_path = str(tmp_path / "processor.db")
    first = ProcessorState(db_path=db_path)
    one = first.file_chargeback("D-DURABLE-CB", "4112", Decimal("340.00"))
    two = first.file_chargeback("D-DURABLE-CB", "4112", Decimal("340.00"))
    assert one == two
    assert first.effect_count("D-DURABLE-CB") == 1
    first.close()

    restarted = ProcessorState(db_path=db_path)
    assert restarted.find_by_case("D-DURABLE-CB") == one
    assert restarted.effect_count("D-DURABLE-CB") == 1


def test_notice_effect_is_durable_and_idempotent(tmp_path: Path) -> None:
    db_path = str(tmp_path / "documents.db")
    first = DocumentSystemState(db_path=db_path)
    one = first.send_notice(
        "D-DURABLE-NOTICE",
        "8830142",
        "REG_E_PROVISIONAL_CREDIT_DISCLOSURE",
        Decimal("340.00"),
        "2026-09-15T17:00:00-04:00",
    )
    two = first.send_notice(
        "D-DURABLE-NOTICE",
        "8830142",
        "REG_E_PROVISIONAL_CREDIT_DISCLOSURE",
        Decimal("340.00"),
        "2026-09-15T17:00:00-04:00",
    )
    assert one == two
    assert first.effect_count("D-DURABLE-NOTICE") == 1
    first.close()

    restarted = DocumentSystemState(db_path=db_path)
    assert restarted.find_by_case("D-DURABLE-NOTICE") == one
    assert restarted.effect_count("D-DURABLE-NOTICE") == 1


def test_each_external_store_survives_independent_process_restart(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    targets = [
        (_write_core_effect, str(tmp_path / "core-process.db")),
        (_write_processor_effect, str(tmp_path / "processor-process.db")),
        (_write_notice_effect, str(tmp_path / "documents-process.db")),
    ]
    for target, db_path in targets:
        process = context.Process(target=target, args=(db_path,))
        process.start()
        process.join(timeout=30)
        assert process.exitcode == 0

    core = CoreBankState(str(tmp_path / "core-process.db"), institution_id="alpha")
    processor = ProcessorState(str(tmp_path / "processor-process.db"))
    documents = DocumentSystemState(str(tmp_path / "documents-process.db"))
    assert core.find_credit_by_case("D-PROCESS-CORE") is not None
    assert processor.find_by_case("D-PROCESS-CB") is not None
    assert documents.find_by_case("D-PROCESS-NOTICE") is not None
