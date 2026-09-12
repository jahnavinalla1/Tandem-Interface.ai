"""Independent durable state for the hostile core-banking simulator."""

from __future__ import annotations

import os
import random
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

from tandem.domain.money import parse_money


@dataclass(frozen=True)
class Transaction:
    txn_id: str
    posted_at: str
    description: str
    amount: Decimal
    card_last4: str
    status: str = "SETTLED"


@dataclass(frozen=True)
class ProvisionalCredit:
    credit_id: str
    case_id: str
    member_id: str
    account_id: str
    amount: Decimal
    memo_code: str
    posted_at: str
    status: str = "POSTED"
    currency: str = "USD"
    business_reference: str = ""
    institution_id: str = "alpha"


@dataclass(frozen=True)
class Member:
    member_id: str
    first_name: str
    last_name: str
    account_id: str
    balance: Decimal
    transactions: List[Transaction] = field(default_factory=list)


class CoreBankState:
    """SQLite-backed external-system state, isolated from Tandem's ledger."""

    def __init__(self, db_path: str | None = None, institution_id: str = "alpha") -> None:
        self.db_path = str(
            Path(db_path or os.environ.get("TANDEM_CORE_SIM_DB", "core_simulator.db")).resolve()
        )
        self.institution_id = institution_id
        self.session_valid = True
        self.require_compliance_interstitial = False
        self.compliance_cleared = False
        self.simulate_latency_ms = 0
        self.simulate_post_commit_delay_ms = 0
        self.fail_credit_lookup_when_present = False
        self._initialize_schema()
        if not self.members:
            self.seed()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS members (
                    institution_id TEXT NOT NULL,
                    member_id TEXT NOT NULL,
                    first_name TEXT NOT NULL,
                    last_name TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    balance TEXT NOT NULL,
                    PRIMARY KEY (institution_id, member_id)
                );
                CREATE TABLE IF NOT EXISTS transactions (
                    institution_id TEXT NOT NULL,
                    member_id TEXT NOT NULL,
                    txn_id TEXT NOT NULL,
                    posted_at TEXT NOT NULL,
                    description TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    card_last4 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    PRIMARY KEY (institution_id, txn_id)
                );
                CREATE TABLE IF NOT EXISTS credits (
                    institution_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    credit_id TEXT NOT NULL,
                    member_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    business_reference TEXT NOT NULL,
                    memo_code TEXT NOT NULL,
                    posted_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    PRIMARY KEY (institution_id, case_id)
                );
                CREATE TABLE IF NOT EXISTS effect_history (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    institution_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    business_reference TEXT NOT NULL,
                    effect_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                """
            )

    def seed(self) -> None:
        """Reset this institution to deterministic seed data."""
        members = [
            (self.institution_id, "8830142", "Jane", "DisputeMember", "CHK-8830142-01", "1240.50"),
            (self.institution_id, "8830124", "John", "ConfusableMember", "CHK-8830124-01", "410.25"),
        ]
        transactions = [
            (
                self.institution_id,
                "8830142",
                "TXN-99101",
                "2026-09-01 14:22:10",
                "POS DEBIT - ELECTRONICS STORE",
                "340.00",
                "4112",
                "SETTLED",
            ),
            (
                self.institution_id,
                "8830142",
                "TXN-99088",
                "2026-08-28 09:15:00",
                "GROCERY MARKET",
                "85.20",
                "4112",
                "SETTLED",
            ),
            (
                self.institution_id,
                "8830124",
                "TXN-99042",
                "2026-08-25 11:05:12",
                "COFFEE SHOP",
                "4.50",
                "8890",
                "SETTLED",
            ),
        ]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for table in ("effect_history", "credits", "transactions", "members"):
                connection.execute(
                    f"DELETE FROM {table} WHERE institution_id = ?", (self.institution_id,)
                )
            connection.executemany("INSERT INTO members VALUES (?, ?, ?, ?, ?, ?)", members)
            connection.executemany(
                "INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?)", transactions
            )
        self.session_valid = True
        self.require_compliance_interstitial = False
        self.compliance_cleared = False
        self.simulate_latency_ms = 0
        self.simulate_post_commit_delay_ms = 0
        self.fail_credit_lookup_when_present = False

    @property
    def members(self) -> Dict[str, Member]:
        with self._connect() as connection:
            member_rows = connection.execute(
                "SELECT * FROM members WHERE institution_id = ?", (self.institution_id,)
            ).fetchall()
            transaction_rows = connection.execute(
                "SELECT * FROM transactions WHERE institution_id = ? ORDER BY posted_at DESC",
                (self.institution_id,),
            ).fetchall()
        transactions: Dict[str, List[Transaction]] = {}
        for row in transaction_rows:
            transactions.setdefault(row["member_id"], []).append(
                Transaction(
                    txn_id=row["txn_id"],
                    posted_at=row["posted_at"],
                    description=row["description"],
                    amount=parse_money(row["amount"]),
                    card_last4=row["card_last4"],
                    status=row["status"],
                )
            )
        return {
            row["member_id"]: Member(
                member_id=row["member_id"],
                first_name=row["first_name"],
                last_name=row["last_name"],
                account_id=row["account_id"],
                balance=parse_money(row["balance"]),
                transactions=transactions.get(row["member_id"], []),
            )
            for row in member_rows
        }

    @property
    def credits(self) -> Dict[str, ProvisionalCredit]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM credits WHERE institution_id = ?", (self.institution_id,)
            ).fetchall()
        return {row["case_id"]: self._credit_from_row(row) for row in rows}

    def get_member(self, member_id: str) -> Member:
        member = self.members.get(member_id)
        if member is None:
            raise KeyError(member_id)
        return member

    def search_members(self, query: str, randomize_order: bool = True) -> List[Member]:
        query = query.strip().lower()
        results = [
            member
            for member in self.members.values()
            if query in member.member_id.lower()
            or query in member.last_name.lower()
            or query in member.account_id.lower()
        ]
        if randomize_order and len(results) > 1:
            random.shuffle(results)
        return results

    def post_credit(
        self,
        case_id: str,
        member_id: str,
        amount: Decimal,
        currency: str = "USD",
        business_reference: str | None = None,
        institution_id: str | None = None,
    ) -> ProvisionalCredit:
        amount = parse_money(amount)
        target_institution = institution_id or self.institution_id
        business_reference = business_reference or case_id
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM credits WHERE institution_id = ? AND case_id = ?",
                (target_institution, case_id),
            ).fetchone()
            if existing:
                credit = self._credit_from_row(existing)
                if (
                    credit.member_id != member_id
                    or credit.amount != amount
                    or credit.currency != currency
                    or credit.business_reference != business_reference
                ):
                    raise ValueError(f"Case {case_id} already has a different credit identity")
                return credit
            member = connection.execute(
                "SELECT * FROM members WHERE institution_id = ? AND member_id = ?",
                (target_institution, member_id),
            ).fetchone()
            if not member:
                raise ValueError(f"Member {member_id} not found")
            credit = ProvisionalCredit(
                credit_id=f"CRD-{random.randint(10000, 99999)}",
                case_id=case_id,
                member_id=member_id,
                account_id=member["account_id"],
                amount=amount,
                memo_code=f"MC-{random.randint(7000, 7999)}",
                posted_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                currency=currency,
                business_reference=business_reference,
                institution_id=target_institution,
            )
            connection.execute(
                "INSERT INTO credits VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    credit.institution_id,
                    credit.case_id,
                    credit.credit_id,
                    credit.member_id,
                    credit.account_id,
                    str(credit.amount),
                    credit.currency,
                    credit.business_reference,
                    credit.memo_code,
                    credit.posted_at,
                    credit.status,
                ),
            )
            new_balance = parse_money(member["balance"]) + amount
            connection.execute(
                "UPDATE members SET balance = ? WHERE institution_id = ? AND member_id = ?",
                (str(new_balance), target_institution, member_id),
            )
            connection.execute(
                """INSERT INTO effect_history
                   (institution_id, case_id, business_reference, effect_type, payload, recorded_at)
                   VALUES (?, ?, ?, 'PROVISIONAL_CREDIT_POSTED', ?, ?)""",
                (
                    target_institution,
                    case_id,
                    business_reference,
                    f"{member_id}|{credit.account_id}|{amount}|{currency}|{credit.memo_code}",
                    credit.posted_at,
                ),
            )
            return credit

    def find_credit_by_case(self, case_id: str) -> Optional[ProvisionalCredit]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM credits WHERE institution_id = ? AND case_id = ?",
                (self.institution_id, case_id),
            ).fetchone()
        return self._credit_from_row(row) if row else None

    def effect_count(self, case_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM effect_history WHERE institution_id = ? AND case_id = ?",
                (self.institution_id, case_id),
            ).fetchone()
        return int(row["count"])

    @staticmethod
    def _credit_from_row(row: sqlite3.Row) -> ProvisionalCredit:
        return ProvisionalCredit(
            credit_id=row["credit_id"],
            case_id=row["case_id"],
            member_id=row["member_id"],
            account_id=row["account_id"],
            amount=parse_money(row["amount"]),
            memo_code=row["memo_code"],
            posted_at=row["posted_at"],
            status=row["status"],
            currency=row["currency"],
            business_reference=row["business_reference"],
            institution_id=row["institution_id"],
        )

    def close(self) -> None:
        """Connections are operation-scoped; retained for lifecycle symmetry."""


core_bank_state = CoreBankState()
