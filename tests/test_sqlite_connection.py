from __future__ import annotations

import sqlite3
from pathlib import Path

from fourseasquant.sqlite_connection import open_database_connection


def test_database_connection_enforces_integrity_without_changing_transactions(
    tmp_path: Path,
) -> None:
    database = tmp_path / "connection-policy.db"
    with sqlite3.connect(database) as connection:
        journal_mode = connection.execute(
            "PRAGMA journal_mode = DELETE"
        ).fetchone()
    assert journal_mode == ("delete",)

    with open_database_connection(database) as connection:
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()
        current_journal_mode = connection.execute(
            "PRAGMA journal_mode"
        ).fetchone()
        connection.execute("CREATE TABLE transaction_probe (value INTEGER)")
        connection.commit()
        connection.execute(
            "INSERT INTO transaction_probe (value) VALUES (1)"
        )

        assert foreign_keys == (1,)
        assert busy_timeout == (30_000,)
        assert current_journal_mode == ("delete",)
        assert connection.in_transaction is True
        connection.rollback()
        assert connection.execute(
            "SELECT COUNT(*) FROM transaction_probe"
        ).fetchone() == (0,)
