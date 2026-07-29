from __future__ import annotations

import sqlite3


IMMUTABLE_CORE_TABLES = (
    "industry_chain_stock_universes",
    "industry_chain_source_registry",
    "industry_chain_evidence",
    "industry_chain_evidence_event_links",
    "industry_chain_events",
    "industry_chain_selection_runs",
    "industry_chain_selections",
    "industry_chain_selection_dismissals",
    "industry_chain_deep_hunt_audits",
    "industry_chain_cleanup_runs",
    "industry_chain_notifications",
)
TRANSIENT_RETENTION_TABLES = (
    "industry_chain_discovery_items",
    "industry_chain_triage_runs",
    "industry_chain_search_runs",
)


def create_industry_chain_core_tables(connection: sqlite3.Connection) -> None:
    """创建第一批追加式产业链表及数据库级防篡改约束。"""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS industry_chain_runtime_control (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
            state TEXT NOT NULL CHECK (
                state IN ('paused', 'running', 'pausing', 'error')
            ),
            paused_at TEXT,
            resumed_at TEXT,
            catchup_from TEXT,
            worker_heartbeat_at TEXT,
            last_poll_at TEXT,
            last_model_run_at TEXT,
            last_cleanup_at TEXT,
            error_summary TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    runtime_columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(industry_chain_runtime_control)"
        )
    }
    if "worker_heartbeat_at" not in runtime_columns:
        connection.execute(
            """
            ALTER TABLE industry_chain_runtime_control
            ADD COLUMN worker_heartbeat_at TEXT
            """
        )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS industry_chain_source_registry (
            source_id TEXT NOT NULL,
            source_version INTEGER NOT NULL CHECK (source_version >= 1),
            source_name TEXT NOT NULL,
            base_url TEXT NOT NULL,
            domain TEXT NOT NULL,
            source_tier INTEGER NOT NULL CHECK (source_tier BETWEEN 1 AND 5),
            source_type TEXT NOT NULL,
            categories_json TEXT NOT NULL CHECK (json_valid(categories_json)),
            access_class TEXT NOT NULL,
            lifecycle_state TEXT NOT NULL CHECK (
                lifecycle_state IN ('active', 'observing', 'disabled')
            ),
            poll_interval_minutes INTEGER NOT NULL
                CHECK (poll_interval_minutes >= 1),
            allow_browser INTEGER NOT NULL CHECK (allow_browser IN (0, 1)),
            config_version TEXT NOT NULL,
            effective_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (source_id, source_version),
            UNIQUE (base_url, source_version)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_industry_chain_sources_active
        ON industry_chain_source_registry (
            lifecycle_state, source_type, source_id, source_version DESC
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS industry_chain_hunting_requests (
            request_id TEXT PRIMARY KEY,
            trigger_method TEXT NOT NULL CHECK (
                trigger_method IN (
                    'automatic', 'manual', 'new_evidence',
                    'historical_replay', 'resume_catchup', 'scheduled_scan'
                )
            ),
            trigger_type TEXT NOT NULL CHECK (
                trigger_type IN (
                    'keyword', 'url', 'message', 'source_event',
                    'catchup_window', 'scheduled_scan'
                )
            ),
            trigger_content TEXT NOT NULL,
            source_url TEXT,
            as_of_time TEXT NOT NULL,
            priority INTEGER NOT NULL CHECK (priority BETWEEN 1 AND 3),
            status TEXT NOT NULL CHECK (
                status IN (
                    'queued', 'running', 'succeeded', 'failed',
                    'cancelled', 'paused'
                )
            ),
            requested_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            error_summary TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS industry_chain_source_checkpoints (
            source_id TEXT PRIMARY KEY,
            last_external_id TEXT,
            last_published_at TEXT,
            last_checked_at TEXT NOT NULL,
            last_status TEXT NOT NULL,
            error_summary TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS industry_chain_discovery_items (
            discovery_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            external_id TEXT NOT NULL,
            security_code TEXT,
            security_name TEXT,
            headline TEXT NOT NULL,
            published_at TEXT NOT NULL,
            collected_at TEXT NOT NULL,
            source_url TEXT NOT NULL,
            attachment_url TEXT,
            content_sha256 TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
            created_at TEXT NOT NULL,
            UNIQUE (source_id, external_id),
            UNIQUE (source_id, content_sha256)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_industry_chain_discovery_time
        ON industry_chain_discovery_items (
            published_at DESC, source_id, external_id
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS industry_chain_event_clusters (
            cluster_id TEXT PRIMARY KEY,
            event_fingerprint TEXT NOT NULL UNIQUE,
            suspected_event_type TEXT NOT NULL,
            canonical_headline TEXT NOT NULL,
            normalized_text TEXT NOT NULL,
            token_json TEXT NOT NULL CHECK (json_valid(token_json)),
            company_key TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_industry_chain_event_clusters_recent
        ON industry_chain_event_clusters (
            last_seen_at DESC, suspected_event_type, cluster_id
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS industry_chain_event_cluster_discoveries (
            cluster_id TEXT NOT NULL,
            discovery_id TEXT NOT NULL UNIQUE,
            linked_at TEXT NOT NULL,
            PRIMARY KEY (cluster_id, discovery_id),
            FOREIGN KEY (cluster_id)
                REFERENCES industry_chain_event_clusters(cluster_id),
            FOREIGN KEY (discovery_id)
                REFERENCES industry_chain_discovery_items(discovery_id)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_industry_chain_cluster_links
        ON industry_chain_event_cluster_discoveries (
            cluster_id, linked_at, discovery_id
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS industry_chain_triage_runs (
            triage_run_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            discovery_ids_json TEXT NOT NULL
                CHECK (json_valid(discovery_ids_json)),
            decisions_json TEXT NOT NULL CHECK (json_valid(decisions_json)),
            model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            model_metrics_json TEXT NOT NULL
                CHECK (json_valid(model_metrics_json)),
            input_sha256 TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (source_id, input_sha256)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_industry_chain_triage_completed
        ON industry_chain_triage_runs (
            completed_at DESC, source_id, triage_run_id
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS industry_chain_search_runs (
            search_run_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            input_text TEXT NOT NULL,
            plan_json TEXT NOT NULL CHECK (json_valid(plan_json)),
            results_json TEXT NOT NULL CHECK (json_valid(results_json)),
            model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            input_sha256 TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (request_id, input_sha256),
            FOREIGN KEY (request_id)
                REFERENCES industry_chain_hunting_requests(request_id)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_industry_chain_search_completed
        ON industry_chain_search_runs (
            completed_at DESC, request_id, search_run_id
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_industry_chain_hunts_queue
        ON industry_chain_hunting_requests (
            status, priority, requested_at, request_id
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS industry_chain_notifications (
            notification_id TEXT PRIMARY KEY,
            notification_type TEXT NOT NULL,
            event_id TEXT,
            selection_id TEXT,
            is_backfill INTEGER NOT NULL CHECK (is_backfill IN (0, 1)),
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
            delivery_status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_industry_chain_notifications_created
        ON industry_chain_notifications (created_at DESC, notification_id)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS industry_chain_stock_universes (
            universe_version TEXT PRIMARY KEY,
            as_of_time TEXT NOT NULL,
            actual_data_date TEXT NOT NULL,
            source TEXT NOT NULL,
            source_published_at TEXT NOT NULL,
            eligibility_rules_version TEXT NOT NULL,
            minimum_listing_trading_days INTEGER NOT NULL,
            security_count INTEGER NOT NULL CHECK (security_count > 0),
            content_sha256 TEXT NOT NULL UNIQUE,
            payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_industry_chain_universes_as_of
        ON industry_chain_stock_universes (
            as_of_time DESC, universe_version
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS industry_chain_evidence (
            evidence_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_name TEXT NOT NULL,
            source_tier INTEGER NOT NULL CHECK (source_tier BETWEEN 1 AND 5),
            source_type TEXT NOT NULL,
            source_url TEXT NOT NULL,
            is_primary INTEGER NOT NULL CHECK (is_primary IN (0, 1)),
            access_class TEXT NOT NULL,
            headline TEXT NOT NULL,
            published_at TEXT NOT NULL,
            updated_at TEXT,
            collected_at TEXT NOT NULL,
            accepted_as_of_time TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            language TEXT NOT NULL,
            is_reprint INTEGER NOT NULL CHECK (is_reprint IN (0, 1)),
            reprint_cluster_id TEXT,
            retention_mode TEXT NOT NULL,
            original_file_path TEXT,
            snapshot_sha256 TEXT NOT NULL,
            snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
            created_at TEXT NOT NULL,
            UNIQUE (source_url, content_sha256),
            UNIQUE (snapshot_sha256)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_industry_chain_evidence_event_time
        ON industry_chain_evidence (event_id, published_at, evidence_id)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS industry_chain_evidence_event_links (
            evidence_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            linked_at TEXT NOT NULL,
            PRIMARY KEY (evidence_id, event_id),
            FOREIGN KEY (evidence_id)
                REFERENCES industry_chain_evidence(evidence_id)
        )
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO industry_chain_evidence_event_links (
            evidence_id, event_id, linked_at
        )
        SELECT evidence_id, event_id, accepted_as_of_time
        FROM industry_chain_evidence
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_industry_chain_evidence_links_event
        ON industry_chain_evidence_event_links (event_id, evidence_id)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS industry_chain_events (
            event_id TEXT NOT NULL,
            event_version INTEGER NOT NULL CHECK (event_version >= 1),
            as_of_time TEXT NOT NULL,
            event_time TEXT NOT NULL,
            evidence_refs_json TEXT NOT NULL
                CHECK (json_valid(evidence_refs_json)),
            event_json TEXT NOT NULL CHECK (json_valid(event_json)),
            content_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (event_id, event_version),
            UNIQUE (event_id, content_sha256)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_industry_chain_events_as_of
        ON industry_chain_events (as_of_time DESC, event_id, event_version DESC)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS industry_chain_selection_runs (
            run_id TEXT PRIMARY KEY,
            selection_id TEXT NOT NULL,
            selection_version INTEGER NOT NULL CHECK (selection_version >= 1),
            event_id TEXT NOT NULL,
            event_version INTEGER NOT NULL CHECK (event_version >= 1),
            trigger_method TEXT NOT NULL,
            as_of_time TEXT NOT NULL,
            rules_version TEXT NOT NULL,
            model_json TEXT NOT NULL CHECK (json_valid(model_json)),
            prompt_version TEXT NOT NULL,
            knowledge_version TEXT NOT NULL,
            stock_universe_version TEXT NOT NULL,
            source_config_version TEXT NOT NULL,
            status TEXT NOT NULL,
            error_summary TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            input_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (selection_id, selection_version),
            FOREIGN KEY (event_id, event_version)
                REFERENCES industry_chain_events(event_id, event_version)
        )
        """
    )
    _migrate_selection_candidate_limit(connection)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS industry_chain_selections (
            selection_id TEXT NOT NULL,
            selection_version INTEGER NOT NULL CHECK (selection_version >= 1),
            run_id TEXT NOT NULL UNIQUE,
            event_id TEXT NOT NULL,
            event_version INTEGER NOT NULL CHECK (event_version >= 1),
            status TEXT NOT NULL,
            candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
            evidence_refs_json TEXT NOT NULL
                CHECK (json_valid(evidence_refs_json)),
            snapshot_sha256 TEXT NOT NULL UNIQUE,
            snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
            published_at TEXT NOT NULL,
            PRIMARY KEY (selection_id, selection_version),
            FOREIGN KEY (run_id)
                REFERENCES industry_chain_selection_runs(run_id),
            FOREIGN KEY (event_id, event_version)
                REFERENCES industry_chain_events(event_id, event_version)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_industry_chain_selections_published
        ON industry_chain_selections (
            published_at DESC, selection_id, selection_version DESC
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS industry_chain_selection_dismissals (
            selection_id TEXT NOT NULL,
            selection_version INTEGER NOT NULL CHECK (selection_version >= 1),
            reason TEXT NOT NULL CHECK (reason IN ('user_deleted')),
            dismissed_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (selection_id, selection_version),
            FOREIGN KEY (selection_id, selection_version)
                REFERENCES industry_chain_selections (
                    selection_id, selection_version
                )
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_industry_chain_dismissals_time
        ON industry_chain_selection_dismissals (
            dismissed_at DESC, selection_id, selection_version
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS industry_chain_deep_hunt_audits (
            request_id TEXT PRIMARY KEY,
            outcome TEXT NOT NULL CHECK (
                outcome IN (
                    'invalid_event', 'policy_only', 'selected',
                    'evidence_insufficient', 'failed'
                )
            ),
            decision_json TEXT CHECK (
                decision_json IS NULL OR json_valid(decision_json)
            ),
            error_summary TEXT,
            model TEXT,
            prompt_version TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_industry_chain_deep_audit_outcome
        ON industry_chain_deep_hunt_audits (
            completed_at DESC, outcome, request_id
        )
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO industry_chain_deep_hunt_audits (
            request_id, outcome, decision_json, error_summary,
            model, prompt_version, started_at, completed_at, created_at
        )
        SELECT
            request.request_id,
            CASE
                WHEN request.status = 'failed' THEN 'failed'
                WHEN selection.status = 'selected' THEN 'selected'
                WHEN selection.status = 'evidence_insufficient'
                    THEN 'evidence_insufficient'
                ELSE 'invalid_event'
            END,
            NULL,
            CASE
                WHEN request.status = 'failed' THEN request.error_summary
                WHEN selection.selection_id IS NULL
                    THEN '历史运行未保存模型决策，结果类型由请求状态推断'
                ELSE NULL
            END,
            json_extract(run.model_json, '$.model_id'),
            COALESCE(run.prompt_version, 'legacy-unrecorded'),
            COALESCE(request.started_at, request.requested_at),
            COALESCE(request.completed_at, request.requested_at),
            COALESCE(request.completed_at, request.requested_at)
        FROM industry_chain_hunting_requests AS request
        LEFT JOIN industry_chain_selections AS selection
          ON selection.selection_id = 'ics-' || request.request_id
        LEFT JOIN industry_chain_selection_runs AS run
          ON run.run_id = selection.run_id
        WHERE request.trigger_type = 'source_event'
          AND request.status IN ('succeeded', 'failed')
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS industry_chain_cleanup_runs (
            cleanup_run_id TEXT PRIMARY KEY,
            policy_version TEXT NOT NULL,
            discovery_cutoff TEXT NOT NULL,
            run_cutoff TEXT NOT NULL,
            deleted_counts_json TEXT NOT NULL
                CHECK (json_valid(deleted_counts_json)),
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_industry_chain_cleanup_completed
        ON industry_chain_cleanup_runs (
            completed_at DESC, cleanup_run_id
        )
        """
    )
    for table in TRANSIENT_RETENTION_TABLES:
        connection.execute(
            f"DROP TRIGGER IF EXISTS {table}_reject_delete"
        )
        connection.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS {table}_reject_update
            BEFORE UPDATE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '产业链历史记录不可修改');
            END
            """
        )
    _create_immutable_triggers(connection)


def _migrate_selection_candidate_limit(
    connection: sqlite3.Connection,
) -> None:
    row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = 'industry_chain_selections'
        """
    ).fetchone()
    if row is None or "BETWEEN 0 AND 3" not in str(row[0]):
        return
    for table in (
        "industry_chain_selections",
        "industry_chain_selection_dismissals",
    ):
        connection.execute(
            f"DROP TRIGGER IF EXISTS {table}_reject_update"
        )
        connection.execute(
            f"DROP TRIGGER IF EXISTS {table}_reject_delete"
        )
    connection.execute(
        """
        CREATE TEMP TABLE selection_rows_v27 AS
        SELECT * FROM industry_chain_selections
        """
    )
    dismissal_exists = connection.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table'
          AND name = 'industry_chain_selection_dismissals'
        """
    ).fetchone()
    if dismissal_exists is not None:
        connection.execute(
            """
            CREATE TEMP TABLE dismissal_rows_v27 AS
            SELECT * FROM industry_chain_selection_dismissals
            """
        )
        connection.execute("DROP TABLE industry_chain_selection_dismissals")
    connection.execute("DROP TABLE industry_chain_selections")
    connection.execute(
        """
        CREATE TABLE industry_chain_selections (
            selection_id TEXT NOT NULL,
            selection_version INTEGER NOT NULL CHECK (selection_version >= 1),
            run_id TEXT NOT NULL UNIQUE,
            event_id TEXT NOT NULL,
            event_version INTEGER NOT NULL CHECK (event_version >= 1),
            status TEXT NOT NULL,
            candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
            evidence_refs_json TEXT NOT NULL
                CHECK (json_valid(evidence_refs_json)),
            snapshot_sha256 TEXT NOT NULL UNIQUE,
            snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
            published_at TEXT NOT NULL,
            PRIMARY KEY (selection_id, selection_version),
            FOREIGN KEY (run_id)
                REFERENCES industry_chain_selection_runs(run_id),
            FOREIGN KEY (event_id, event_version)
                REFERENCES industry_chain_events(event_id, event_version)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO industry_chain_selections
        SELECT * FROM selection_rows_v27
        """
    )
    connection.execute("DROP TABLE selection_rows_v27")
    if dismissal_exists is not None:
        connection.execute(
            """
            CREATE TABLE industry_chain_selection_dismissals (
                selection_id TEXT NOT NULL,
                selection_version INTEGER NOT NULL
                    CHECK (selection_version >= 1),
                reason TEXT NOT NULL CHECK (reason IN ('user_deleted')),
                dismissed_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (selection_id, selection_version),
                FOREIGN KEY (selection_id, selection_version)
                    REFERENCES industry_chain_selections (
                        selection_id, selection_version
                    )
            )
            """
        )
        connection.execute(
            """
            INSERT INTO industry_chain_selection_dismissals
            SELECT * FROM dismissal_rows_v27
            """
        )
        connection.execute("DROP TABLE dismissal_rows_v27")


def _create_immutable_triggers(connection: sqlite3.Connection) -> None:
    for table in IMMUTABLE_CORE_TABLES:
        connection.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS {table}_reject_update
            BEFORE UPDATE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '产业链历史记录不可修改');
            END
            """
        )
        connection.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS {table}_reject_delete
            BEFORE DELETE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '产业链历史记录不可删除');
            END
            """
        )
