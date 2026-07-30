from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fourseasquant.database import (
    HistoricalSecurityFactRow,
    TechnicalScorePublicationRow,
    claim_automation_date,
    completed_history_symbols,
    create_task_run,
    historical_security_facts_for_date,
    initialize_database,
    latest_market_facts,
    publish_snapshot,
    release_automation_date,
    renew_automation_date_claim,
    save_history_symbol_batch,
    save_market_facts,
)


def test_version_one_task_history_is_migrated_with_meaningful_stages(
    tmp_path: Path,
) -> None:
    database = tmp_path / "version-one.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO app_metadata VALUES ('schema_version', '1')"
        )
        connection.execute(
            """
            CREATE TABLE task_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger_method TEXT NOT NULL,
                target_date TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO task_runs (
                trigger_method, target_date, started_at, finished_at, status
            ) VALUES (
                'manual', '2026-07-20', '2026-07-20T16:30:00+08:00',
                '2026-07-20T16:31:00+08:00', 'succeeded'
            )
            """
        )

    initialize_database(database)

    with sqlite3.connect(database) as connection:
        version = connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'schema_version'"
        ).fetchone()
        stage = connection.execute("SELECT stage FROM task_runs").fetchone()
    assert version == ("32",)
    assert stage == ("completed",)


def test_version_two_automation_claims_gain_owned_claim_ids(tmp_path: Path) -> None:
    database = tmp_path / "version-two.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO app_metadata VALUES ('schema_version', '2')")
        connection.execute(
            """
            CREATE TABLE automation_claims (
                target_date TEXT PRIMARY KEY,
                claimed_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO automation_claims VALUES ('2026-07-21', '2026-07-21T16:30:00+08:00')"
        )

    initialize_database(database)

    with sqlite3.connect(database) as connection:
        version = connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'schema_version'"
        ).fetchone()
        claim = connection.execute(
            "SELECT target_date, claim_id FROM automation_claims"
        ).fetchone()
    assert version == ("32",)
    assert claim is not None
    assert claim[0] == "2026-07-21"
    assert claim[1]


def test_version_thirty_one_adds_idempotent_automation_schedule_slots(
    tmp_path: Path,
) -> None:
    database = tmp_path / "version-thirty-one.db"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE market_automation_schedule_slots")
        connection.execute("DROP TABLE fundamental_automation_schedule_slots")
        connection.execute(
            """
            UPDATE app_metadata
            SET value = '31'
            WHERE key = 'schema_version'
            """
        )

    initialize_database(database)

    with sqlite3.connect(database) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name IN (
                    'market_automation_schedule_slots',
                    'fundamental_automation_schedule_slots'
                  )
                """
            )
        }
        version = connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'schema_version'"
        ).fetchone()

    assert tables == {
        "market_automation_schedule_slots",
        "fundamental_automation_schedule_slots",
    }
    assert version == ("32",)


def test_stale_owner_cannot_release_newer_automation_claim(tmp_path: Path) -> None:
    database = tmp_path / "claim-owner.db"
    initialize_database(database)
    target = date(2026, 7, 21)
    started = datetime(2026, 7, 21, 16, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    first_claim = claim_automation_date(database, target, started)
    assert first_claim is not None
    second_claim = claim_automation_date(
        database,
        target,
        started + timedelta(seconds=7_201),
    )
    assert second_claim is not None

    release_automation_date(database, target, first_claim)
    third_claim = claim_automation_date(
        database,
        target,
        started + timedelta(seconds=7_202),
    )
    assert third_claim is None

    release_automation_date(database, target, second_claim)


def test_replacing_stale_claim_marks_interrupted_task_failed(tmp_path: Path) -> None:
    database = tmp_path / "stale-running-task.db"
    initialize_database(database)
    target = date(2026, 7, 27)
    started = datetime(2026, 7, 27, 20, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    first_claim = claim_automation_date(database, target, started)
    assert first_claim is not None
    task_id = create_task_run(
        database,
        target,
        started,
        trigger_method="retry",
    )

    replacement = claim_automation_date(
        database,
        target,
        started + timedelta(seconds=7_201),
    )

    assert replacement is not None
    with sqlite3.connect(database) as connection:
        task = connection.execute(
            """
            SELECT status, finished_at, error_summary
            FROM task_runs
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()
    assert task is not None
    assert task[0] == "failed"
    assert task[1] is not None
    assert task[2] == "任务进程中断，运行租约已过期"


def test_renewed_automation_claim_cannot_be_taken_over_after_two_hours(
    tmp_path: Path,
) -> None:
    database = tmp_path / "renewed-claim.db"
    initialize_database(database)
    target = date(2026, 7, 24)
    started = datetime(2026, 7, 24, 16, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    claim_id = claim_automation_date(database, target, started)
    assert claim_id is not None

    renewed = renew_automation_date_claim(
        database,
        target,
        claim_id,
        started + timedelta(seconds=7_199),
    )
    competing_claim = claim_automation_date(
        database,
        target,
        started + timedelta(seconds=7_201),
    )

    assert renewed is True
    assert competing_claim is None
    release_automation_date(database, target, claim_id)


def test_version_fifteen_capital_batches_migrate_to_unknown_insider_window(
    tmp_path: Path,
) -> None:
    database = tmp_path / "version-fifteen.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO app_metadata VALUES ('schema_version', '15')"
        )
        connection.execute(
            """
            CREATE TABLE capital_action_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                as_of_date TEXT NOT NULL,
                expected_codes_json TEXT NOT NULL,
                completed_codes_json TEXT NOT NULL,
                status TEXT NOT NULL,
                errors_json TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                published_at TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO capital_action_batches (
                as_of_date, expected_codes_json, completed_codes_json,
                status, errors_json, collected_at, published_at
            ) VALUES (
                '2026-07-24', '["600000"]', '["600000"]',
                'published', '{}', '2026-07-25T16:30:00+08:00',
                '2026-07-25T16:30:00+08:00'
            )
            """
        )

    initialize_database(database)

    with sqlite3.connect(database) as connection:
        migrated = connection.execute(
            """
            SELECT insider_window_complete
            FROM capital_action_batches
            WHERE id = 1
            """
        ).fetchone()
        version = connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'schema_version'"
        ).fetchone()
    assert migrated == (0,)
    assert version == ("32",)


def test_snapshot_and_technical_publication_activate_atomically(
    tmp_path: Path,
) -> None:
    database = tmp_path / "atomic-publication.db"
    initialize_database(database)
    target = date(2026, 7, 21)
    published_at = datetime(
        2026, 7, 21, 16, 31, tzinfo=ZoneInfo("Asia/Shanghai")
    )
    task_id = create_task_run(database, target, published_at)
    publication = TechnicalScorePublicationRow(
        version="technical-v1",
        official_start=date(2025, 7, 22),
        official_end=target,
        qfq_source="akshare_sina_daily_qfq:2026-07-21",
        symbol_count=4_900,
        score_count=1_000_000,
        published_at=published_at,
    )

    try:
        publish_snapshot(
            database,
            task_id=task_id,
            target_date=target,
            payload_json='{"status":"complete"}',
            published_at=published_at,
            simulate_failure=True,
            technical_publication=publication,
        )
    except RuntimeError:
        pass

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM daily_snapshots"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM technical_score_publications"
        ).fetchone() == (0,)

    publish_snapshot(
        database,
        task_id=task_id,
        target_date=target,
        payload_json='{"status":"complete"}',
        published_at=published_at,
        technical_publication=publication,
    )

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM daily_snapshots"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT qfq_source FROM technical_score_publications"
        ).fetchone() == (publication.qfq_source,)


def test_market_facts_keep_changed_revisions_without_duplicating_same_content(
    tmp_path: Path,
) -> None:
    database = tmp_path / "market-facts.db"
    initialize_database(database)
    actual_data_date = date(2026, 7, 21)
    first_collected_at = datetime(
        2026, 7, 21, 16, 31, tzinfo=ZoneInfo("Asia/Shanghai")
    )
    revised_collected_at = first_collected_at + timedelta(minutes=2)

    first_id = save_market_facts(
        database,
        actual_data_date=actual_data_date,
        source="akshare",
        payload_json='{"securities":[{"code":"000001","close":12.34}]}',
        collected_at=first_collected_at,
    )
    duplicate_id = save_market_facts(
        database,
        actual_data_date=actual_data_date,
        source="akshare",
        payload_json='{"securities":[{"code":"000001","close":12.34}]}',
        collected_at=first_collected_at + timedelta(minutes=1),
    )
    revised_id = save_market_facts(
        database,
        actual_data_date=actual_data_date,
        source="akshare",
        payload_json='{"securities":[{"code":"000001","close":12.35}]}',
        collected_at=revised_collected_at,
    )

    assert duplicate_id == first_id
    assert revised_id != first_id
    with sqlite3.connect(database) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM market_fact_snapshots"
        ).fetchone()
    assert count == (2,)

    latest = latest_market_facts(database, actual_data_date)
    assert latest is not None
    assert latest.id == revised_id
    assert latest.actual_data_date == actual_data_date
    assert latest.source == "akshare"
    assert latest.payload_json.endswith('"close":12.35}]}')
    assert latest.collected_at == revised_collected_at


def test_history_symbol_batch_is_atomic_idempotent_and_resumable(
    tmp_path: Path,
) -> None:
    database = tmp_path / "history.db"
    initialize_database(database)
    range_start = date(2025, 7, 22)
    range_end = date(2026, 7, 21)
    collected_at = datetime(
        2026, 7, 22, 16, 30, tzinfo=ZoneInfo("Asia/Shanghai")
    )
    row = HistoricalSecurityFactRow(
        actual_data_date=date(2026, 7, 21),
        code="000001",
        name="平安银行",
        open=10.99,
        high=11.13,
        low=10.83,
        close=10.84,
        previous_close=10.98,
        change_pct=-1.275,
        volume=175_511_288,
        turnover_cny=1_925_298_925,
        listing_trading_days=8_000,
    )

    save_history_symbol_batch(
        database,
        source="akshare_sina_daily",
        range_start=range_start,
        range_end=range_end,
        code="000001",
        facts=[row],
        completed_at=collected_at,
    )
    save_history_symbol_batch(
        database,
        source="akshare_sina_daily",
        range_start=range_start,
        range_end=range_end,
        code="000001",
        facts=[row],
        completed_at=collected_at,
    )

    assert completed_history_symbols(
        database,
        source="akshare_sina_daily",
        range_start=range_start,
        range_end=range_end,
    ) == {"000001"}
    facts = historical_security_facts_for_date(
        database,
        source="akshare_sina_daily",
        actual_data_date=date(2026, 7, 21),
    )
    assert facts == [row]
    with sqlite3.connect(database) as connection:
        fact_count = connection.execute(
            "SELECT COUNT(*) FROM historical_security_facts"
        ).fetchone()
        progress_count = connection.execute(
            "SELECT COUNT(*) FROM history_ingestion_progress"
        ).fetchone()
    assert fact_count == (1,)
    assert progress_count == (1,)
