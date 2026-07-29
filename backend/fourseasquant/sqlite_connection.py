from __future__ import annotations

import sqlite3
from os import PathLike


DEFAULT_BUSY_TIMEOUT_SECONDS = 30.0
DEFAULT_BUSY_TIMEOUT_MILLISECONDS = int(
    DEFAULT_BUSY_TIMEOUT_SECONDS * 1_000
)


def open_database_connection(
    database: str | PathLike[str],
) -> sqlite3.Connection:
    """打开保持既有事务语义、统一外键与锁等待策略的连接。"""

    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            f"PRAGMA busy_timeout = {DEFAULT_BUSY_TIMEOUT_MILLISECONDS}"
        )
    except sqlite3.Error:
        connection.close()
        raise
    return connection
