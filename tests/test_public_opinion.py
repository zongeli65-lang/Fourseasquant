from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from fourseasquant.public_opinion import (
    ModelOpinionClassification,
    ModelOpinionLabel,
    OpinionContent,
    aggregate_public_opinion,
)
from fourseasquant.public_opinion_repository import (
    HotDiscoveryStock,
    add_to_opinion_watchlist,
    list_opinion_watchlist,
    list_public_opinion_jobs,
    mark_public_opinion_deleted,
    read_cached_model_classifications,
    read_public_opinion_overview,
    read_public_opinion_window,
    read_strategy_opinion_targets,
    rebuild_public_opinion_day,
    read_public_opinion_universe,
    remove_from_opinion_watchlist,
    save_public_opinion_day,
    schedule_automatic_collection_jobs,
    sync_hot_discovery_snapshot,
    publish_strategy_opinion_targets,
)
from fourseasquant.database import initialize_database


BEIJING = ZoneInfo("Asia/Shanghai")


def _content(
    index: int,
    text: str,
    *,
    likes: int | None = 0,
) -> OpinionContent:
    return OpinionContent(
        platform="eastmoney",
        content_id=f"post-{index}",
        code="000001",
        url=f"https://example.test/post/{index}",
        published_at=datetime(2026, 7, 26, 10, index, tzinfo=BEIJING),
        collected_at=datetime(2026, 7, 26, 18, 30, tzinfo=BEIJING),
        likes=likes,
        content_kind="topic",
        source_type="user_original",
        text=text,
    )


def test_complete_platform_day_publishes_direction_from_valid_content() -> None:
    contents = [
        *[_content(index, "盈利增长，明显利好") for index in range(8)],
        *[_content(index + 8, "亏损扩大，属于利空") for index in range(2)],
        _content(20, "今天只是路过看看"),
    ]

    result = aggregate_public_opinion(
        contents,
        actual_date=date(2026, 7, 26),
        platform="eastmoney",
        code="000001",
        collection_complete=True,
        rules_version="public-opinion-v1",
    )

    assert result.direction_status == "published"
    assert result.direction == "favorable"
    assert result.valid_count == 10
    assert result.unknown_count == 1
    assert result.direction_index == pytest.approx(0.6)


def test_model_classification_counts_neutral_as_relevant_not_invalid() -> None:
    contents = [_content(index, f"帖子 {index}") for index in range(11)]
    labels: list[ModelOpinionLabel] = [
        "favorable",
        "unfavorable",
        "neutral",
        "neutral",
        "neutral",
        "neutral",
        "neutral",
        "neutral",
        "neutral",
        "neutral",
        "unrelated",
    ]
    classifications = {
        content.content_id: ModelOpinionClassification(
            content_id=content.content_id,
            label=label,
            confidence=0.9,
            model="deepseek-v4-pro",
            prompt_version="public-opinion-deepseek-v1",
        )
        for content, label in zip(contents, labels, strict=True)
    }

    result = aggregate_public_opinion(
        contents,
        classifications=classifications,
        actual_date=date(2026, 7, 26),
        platform="eastmoney",
        code="000001",
        collection_complete=True,
        rules_version="public-opinion-deepseek-v1",
    )

    assert result.classified_count == 11
    assert result.valid_count == 10
    assert result.neutral_count == 8
    assert result.unrelated_count == 1
    assert result.direction_status == "published"
    assert result.direction == "balanced"
    assert result.direction_index == pytest.approx(0)


def test_capped_model_sample_remains_publishable_and_discloses_limit() -> None:
    contents = [_content(index, f"帖子 {index}") for index in range(10)]
    classifications = {
        content.content_id: ModelOpinionClassification(
            content_id=content.content_id,
            label="favorable",
            confidence=0.9,
            model="deepseek-v4-pro",
            prompt_version="public-opinion-deepseek-v1",
        )
        for content in contents
    }

    result = aggregate_public_opinion(
        contents,
        classifications=classifications,
        actual_date=date(2026, 7, 26),
        platform="eastmoney",
        code="000001",
        collection_complete=True,
        sample_capped=True,
        sample_limit=300,
        rules_version="public-opinion-deepseek-v1",
    )

    assert result.direction_status == "published"
    assert result.sample_capped is True
    assert result.sample_limit == 300


def test_incomplete_or_small_collection_never_publishes_direction() -> None:
    small = [_content(index, "明确利好") for index in range(9)]
    incomplete = [_content(index, "明确利好") for index in range(10)]

    small_result = aggregate_public_opinion(
        small,
        actual_date=date(2026, 7, 26),
        platform="eastmoney",
        code="000001",
        collection_complete=True,
        rules_version="public-opinion-v1",
    )
    incomplete_result = aggregate_public_opinion(
        incomplete,
        actual_date=date(2026, 7, 26),
        platform="eastmoney",
        code="000001",
        collection_complete=False,
        rules_version="public-opinion-v1",
    )

    assert small_result.direction_status == "insufficient_sample"
    assert small_result.direction is None
    assert incomplete_result.direction_status == "collecting"
    assert incomplete_result.direction is None


def test_user_explicitly_manages_persistent_opinion_watchlist(
    tmp_path: Path,
) -> None:
    path = tmp_path / "opinion.db"
    initialize_database(path)

    add_to_opinion_watchlist(
        path,
        code="000001",
        name="平安银行",
        now=datetime(2026, 7, 26, 12, 0, tzinfo=BEIJING),
    )
    assert [(item.code, item.active) for item in list_opinion_watchlist(path)] == [
        ("000001", True)
    ]

    remove_from_opinion_watchlist(
        path,
        code="000001",
        now=datetime(2026, 7, 26, 12, 30, tzinfo=BEIJING),
    )
    assert [(item.code, item.active) for item in list_opinion_watchlist(path)] == [
        ("000001", False)
    ]


def test_public_opinion_storage_keeps_references_not_body_and_exposes_platform_day(
    tmp_path: Path,
) -> None:
    path = tmp_path / "opinion-storage.db"
    initialize_database(path)
    now = datetime(2026, 7, 26, 20, 0, tzinfo=BEIJING)
    add_to_opinion_watchlist(
        path,
        code="000001",
        name="平安银行",
        now=now,
    )
    contents = [_content(index, "盈利增长，明显利好") for index in range(10)]
    classifications = {
        content.content_id: ModelOpinionClassification(
            content_id=content.content_id,
            label="favorable",
            confidence=0.94,
            model="deepseek-v4-pro",
            prompt_version="public-opinion-deepseek-v1",
        )
        for content in contents
    }
    aggregate = aggregate_public_opinion(
        contents,
        classifications=classifications,
        actual_date=date(2026, 7, 26),
        platform="eastmoney",
        code="000001",
        collection_complete=True,
        rules_version="public-opinion-v1",
    )

    save_public_opinion_day(
        path,
        contents=contents,
        classifications=classifications,
        aggregate=aggregate,
        collected_at=now,
    )

    import sqlite3

    with sqlite3.connect(path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(public_opinion_content_references)"
            )
        }
        stored = connection.execute(
            """
            SELECT content_id, sentiment, likes, content_sha256,
                   classification_model, classification_confidence,
                   prompt_version
            FROM public_opinion_content_references
            ORDER BY content_id
            """
        ).fetchall()
    overview = read_public_opinion_overview(path)

    assert "text" not in columns
    assert len(stored) == 10
    assert stored[0][1] == "favorable"
    assert len(stored[0][3]) == 64
    assert stored[0][4:] == (
        "deepseek-v4-pro",
        0.94,
        "public-opinion-deepseek-v1",
    )
    assert overview[0].eastmoney is not None
    assert overview[0].eastmoney.direction == "favorable"
    assert overview[0].tonghuashun is None
    cached = read_cached_model_classifications(
        path,
        contents=contents,
        model="deepseek-v4-pro",
        prompt_version="public-opinion-deepseek-v1",
    )
    assert cached == classifications


def test_rebuilt_model_day_counts_neutral_content_as_relevant(
    tmp_path: Path,
) -> None:
    path = tmp_path / "opinion-model-rebuild.db"
    initialize_database(path)
    now = datetime(2026, 7, 26, 20, tzinfo=BEIJING)
    contents = [_content(index, f"帖子 {index}") for index in range(10)]
    classifications = {
        content.content_id: ModelOpinionClassification(
            content_id=content.content_id,
            label="favorable" if index == 0 else "neutral",
            confidence=0.9,
            model="deepseek-v4-pro",
            prompt_version="public-opinion-deepseek-v1",
        )
        for index, content in enumerate(contents)
    }
    aggregate = aggregate_public_opinion(
        contents,
        classifications=classifications,
        actual_date=date(2026, 7, 26),
        platform="eastmoney",
        code="000001",
        collection_complete=False,
        rules_version="public-opinion-deepseek-v1",
    )
    save_public_opinion_day(
        path,
        contents=contents,
        classifications=classifications,
        aggregate=aggregate,
        collected_at=now,
    )

    rebuilt = rebuild_public_opinion_day(
        path,
        platform="eastmoney",
        code="000001",
        actual_date=date(2026, 7, 26),
        collection_complete=True,
        collected_at=now,
        rules_version="public-opinion-deepseek-v1",
    )

    assert rebuilt.classified_count == 10
    assert rebuilt.valid_count == 10
    assert rebuilt.neutral_count == 9
    assert rebuilt.direction_status == "published"
    assert rebuilt.direction == "balanced"


def test_deepseek_rebuild_excludes_legacy_classifications(
    tmp_path: Path,
) -> None:
    path = tmp_path / "opinion-version-isolation.db"
    initialize_database(path)
    now = datetime(2026, 7, 26, 20, tzinfo=BEIJING)
    legacy_contents = [
        _content(index, "盈利增长，明显利好")
        for index in range(10)
    ]
    legacy_aggregate = aggregate_public_opinion(
        legacy_contents,
        actual_date=date(2026, 7, 26),
        platform="eastmoney",
        code="000001",
        collection_complete=True,
        rules_version="public-opinion-v1",
    )
    save_public_opinion_day(
        path,
        contents=legacy_contents,
        aggregate=legacy_aggregate,
        collected_at=now,
    )
    model_contents = [
        _content(index + 20, f"新版帖子 {index}")
        for index in range(3)
    ]
    classifications = {
        content.content_id: ModelOpinionClassification(
            content_id=content.content_id,
            label="neutral",
            confidence=0.9,
            model="deepseek-v4-pro",
            prompt_version="public-opinion-deepseek-v1",
        )
        for content in model_contents
    }
    model_aggregate = aggregate_public_opinion(
        model_contents,
        classifications=classifications,
        actual_date=date(2026, 7, 26),
        platform="eastmoney",
        code="000001",
        collection_complete=False,
        rules_version="public-opinion-deepseek-v1",
    )
    save_public_opinion_day(
        path,
        contents=model_contents,
        classifications=classifications,
        aggregate=model_aggregate,
        collected_at=now,
    )

    rebuilt = rebuild_public_opinion_day(
        path,
        platform="eastmoney",
        code="000001",
        actual_date=date(2026, 7, 26),
        collection_complete=True,
        collected_at=now,
        rules_version="public-opinion-deepseek-v1",
    )

    assert rebuilt.content_count == 3
    assert rebuilt.classified_count == 3
    assert rebuilt.neutral_count == 3
    assert rebuilt.direction_status == "insufficient_sample"
    window = read_public_opinion_window(
        path,
        platform="eastmoney",
        code="000001",
        end_date=date(2026, 7, 26),
        days=1,
        rules_version="public-opinion-deepseek-v1",
    )
    assert window.content_count == 3
    assert window.classified_count == 3


def test_complete_hot_snapshots_keep_fallen_stock_for_five_trading_days(
    tmp_path: Path,
) -> None:
    path = tmp_path / "opinion-discovery.db"
    initialize_database(path)
    sync_hot_discovery_snapshot(
        path,
        platform="eastmoney",
        actual_date=date(2026, 7, 24),
        stocks=[
            HotDiscoveryStock(code="000001", name="平安银行", rank=1),
            HotDiscoveryStock(code="600000", name="浦发银行", rank=2),
        ],
        collected_at=datetime(2026, 7, 24, 18, 0, tzinfo=BEIJING),
        complete=True,
    )
    sync_hot_discovery_snapshot(
        path,
        platform="eastmoney",
        actual_date=date(2026, 7, 27),
        stocks=[HotDiscoveryStock(code="600000", name="浦发银行", rank=1)],
        collected_at=datetime(2026, 7, 27, 18, 0, tzinfo=BEIJING),
        complete=True,
    )

    retained = read_public_opinion_universe(path, as_of_date=date(2026, 7, 31))
    expired = read_public_opinion_universe(path, as_of_date=date(2026, 8, 3))

    assert [(item.code, item.entry_reasons) for item in retained] == [
        ("600000", ["eastmoney_hot"]),
        ("000001", ["eastmoney_hot"]),
    ]
    assert [item.code for item in expired] == ["600000"]


def test_overview_excludes_hot_discovery_and_prioritizes_strategy_targets(
    tmp_path: Path,
) -> None:
    path = tmp_path / "opinion-overview-discovery.db"
    initialize_database(path)
    now = datetime(2026, 7, 26, 18, 0, tzinfo=BEIJING)
    sync_hot_discovery_snapshot(
        path,
        platform="eastmoney",
        actual_date=date(2026, 7, 26),
        stocks=[HotDiscoveryStock(code="000001", name="平安银行", rank=1)],
        collected_at=now,
        complete=True,
    )
    add_to_opinion_watchlist(
        path,
        code="000001",
        name="平安银行",
        now=now,
    )
    publish_strategy_opinion_targets(
        path,
        actual_date=date(2026, 7, 26),
        strategy_version="core-strategy-v1",
        codes=["000001"],
        published_at=now,
    )

    overview = read_public_opinion_overview(
        path,
        as_of_date=date(2026, 7, 26),
    )

    assert len(overview) == 1
    assert overview[0].entry_reason == "strategy_target"
    assert overview[0].entry_reasons == [
        "strategy_target",
        "explicit_watchlist",
    ]


def test_strategy_target_snapshot_is_ordered_immutable_and_capped_at_ten(
    tmp_path: Path,
) -> None:
    path = tmp_path / "opinion-strategy-targets.db"
    initialize_database(path)
    now = datetime(2026, 7, 26, 18, 30, tzinfo=BEIJING)
    published = publish_strategy_opinion_targets(
        path,
        actual_date=date(2026, 7, 26),
        strategy_version="core-strategy-v1",
        codes=["600000", "000001"],
        published_at=now,
    )
    repeated = publish_strategy_opinion_targets(
        path,
        actual_date=date(2026, 7, 26),
        strategy_version="core-strategy-v1",
        codes=["600000", "000001"],
        published_at=now,
    )

    assert published.codes == ["600000", "000001"]
    assert repeated == published
    assert read_strategy_opinion_targets(
        path,
        actual_date=date(2026, 7, 26),
    ) == published
    with pytest.raises(ValueError, match="新策略版本"):
        publish_strategy_opinion_targets(
            path,
            actual_date=date(2026, 7, 26),
            strategy_version="core-strategy-v1",
            codes=["000001"],
            published_at=now,
        )
    with pytest.raises(ValueError, match="最多发布 10 只"):
        publish_strategy_opinion_targets(
            path,
            actual_date=date(2026, 7, 27),
            strategy_version="core-strategy-v1",
            codes=[f"{index:06d}" for index in range(11)],
            published_at=now,
        )


def test_daily_scheduler_uses_only_strategy_targets_and_backfills_three_days(
    tmp_path: Path,
) -> None:
    path = tmp_path / "opinion-schedule.db"
    initialize_database(path)
    now = datetime(2026, 7, 26, 18, 30, tzinfo=BEIJING)
    add_to_opinion_watchlist(
        path,
        code="000001",
        name="平安银行",
        now=now,
    )
    sync_hot_discovery_snapshot(
        path,
        platform="eastmoney",
        actual_date=date(2026, 7, 26),
        stocks=[HotDiscoveryStock(code="000001", name="平安银行", rank=1)],
        collected_at=now,
        complete=True,
    )
    assert schedule_automatic_collection_jobs(
        path,
        actual_date=date(2026, 7, 26),
        now=now,
    ) == []
    publish_strategy_opinion_targets(
        path,
        actual_date=date(2026, 7, 26),
        strategy_version="core-strategy-v1",
        codes=["600000", "000001"],
        published_at=now,
    )

    first = schedule_automatic_collection_jobs(
        path,
        actual_date=date(2026, 7, 26),
        now=now,
    )
    repeated = schedule_automatic_collection_jobs(
        path,
        actual_date=date(2026, 7, 26),
        now=now,
    )

    assert [
        (
            job.code,
            job.platform,
            job.start_date,
            job.end_date,
            job.trigger,
            job.classification_version,
            job.target_source,
            job.target_version,
        )
        for job in first
    ] == [
        (
            "600000",
            "sina",
            date(2026, 7, 24),
            date(2026, 7, 26),
            "automatic",
            "public-opinion-deepseek-v1",
            "strategy",
            "core-strategy-v1",
        ),
        (
            "000001",
            "sina",
            date(2026, 7, 24),
            date(2026, 7, 26),
            "automatic",
            "public-opinion-deepseek-v1",
            "strategy",
            "core-strategy-v1",
        ),
    ]
    assert repeated == []


def test_three_and_seven_day_windows_publish_only_when_every_calendar_day_complete(
    tmp_path: Path,
) -> None:
    path = tmp_path / "opinion-window.db"
    initialize_database(path)
    for day in (24, 25, 26):
        actual_date = date(2026, 7, day)
        contents = [
            _content(index, "盈利增长，明显利好").model_copy(
                update={
                    "content_id": f"{day}-{index}",
                    "published_at": datetime(
                        2026, 7, day, 10, index, tzinfo=BEIJING
                    ),
                }
            )
            for index in range(4)
        ]
        aggregate = aggregate_public_opinion(
            contents,
            actual_date=actual_date,
            platform="eastmoney",
            code="000001",
            collection_complete=True,
            rules_version="public-opinion-v1",
        )
        save_public_opinion_day(
            path,
            contents=contents,
            aggregate=aggregate,
            collected_at=datetime(2026, 7, 26, 20, tzinfo=BEIJING),
        )

    three_days = read_public_opinion_window(
        path,
        platform="eastmoney",
        code="000001",
        end_date=date(2026, 7, 26),
        days=3,
        rules_version="public-opinion-v1",
    )
    seven_days = read_public_opinion_window(
        path,
        platform="eastmoney",
        code="000001",
        end_date=date(2026, 7, 26),
        days=7,
        rules_version="public-opinion-v1",
    )

    assert three_days.completed_day_count == 3
    assert three_days.content_count == 12
    assert three_days.direction_status == "published"
    assert three_days.direction == "favorable"
    assert seven_days.completed_day_count == 3
    assert seven_days.direction_status == "collecting"
    assert seven_days.direction is None


def test_likes_refresh_for_three_calendar_days_then_freeze_and_late_delete_is_audit_only(
    tmp_path: Path,
) -> None:
    path = tmp_path / "opinion-freeze.db"
    initialize_database(path)
    original = _content(1, "明确利好", likes=1).model_copy(
        update={
            "published_at": datetime(2026, 7, 24, 10, tzinfo=BEIJING),
            "collected_at": datetime(2026, 7, 24, 18, tzinfo=BEIJING),
        }
    )

    def save(content: OpinionContent, collected_at: datetime) -> None:
        aggregate = aggregate_public_opinion(
            [content],
            actual_date=date(2026, 7, 24),
            platform="eastmoney",
            code="000001",
            collection_complete=True,
            rules_version="public-opinion-v1",
        )
        save_public_opinion_day(
            path,
            contents=[content],
            aggregate=aggregate,
            collected_at=collected_at,
        )

    save(original, original.collected_at)
    within_window = original.model_copy(
        update={
            "likes": 10,
            "collected_at": datetime(2026, 7, 26, 18, tzinfo=BEIJING),
        }
    )
    save(within_window, within_window.collected_at)
    after_window = original.model_copy(
        update={
            "likes": 99,
            "collected_at": datetime(2026, 7, 27, 1, tzinfo=BEIJING),
        }
    )
    save(after_window, after_window.collected_at)
    mark_public_opinion_deleted(
        path,
        platform="eastmoney",
        content_id=original.content_id,
        code="000001",
        detected_at=datetime(2026, 7, 28, 12, tzinfo=BEIJING),
    )

    import sqlite3

    with sqlite3.connect(path) as connection:
        stored = connection.execute(
            """
            SELECT likes, frozen_at, deleted_at
            FROM public_opinion_content_references
            """
        ).fetchone()
    assert stored is not None
    assert stored[0] == 10
    assert stored[1].startswith("2026-07-27T00:00:00")
    assert stored[2].startswith("2026-07-28T12:00:00")


def test_frozen_legacy_reference_can_receive_versioned_model_classification(
    tmp_path: Path,
) -> None:
    path = tmp_path / "opinion-frozen-reclassify.db"
    initialize_database(path)
    original = _content(1, "旧解析内容", likes=3).model_copy(
        update={
            "published_at": datetime(2026, 7, 20, 10, tzinfo=BEIJING),
            "collected_at": datetime(2026, 7, 23, 18, tzinfo=BEIJING),
        }
    )
    legacy_aggregate = aggregate_public_opinion(
        [original],
        actual_date=date(2026, 7, 20),
        platform="eastmoney",
        code="000001",
        collection_complete=True,
        rules_version="public-opinion-v1",
    )
    save_public_opinion_day(
        path,
        contents=[original],
        aggregate=legacy_aggregate,
        collected_at=original.collected_at,
    )

    corrected = original.model_copy(
        update={
            "text": "公司利润增长，订单改善",
            "likes": 99,
            "collected_at": datetime(2026, 7, 27, 18, tzinfo=BEIJING),
        }
    )
    classification = ModelOpinionClassification(
        content_id=corrected.content_id,
        label="favorable",
        confidence=0.91,
        model="deepseek-v4-pro",
        prompt_version="public-opinion-deepseek-v1",
    )
    model_aggregate = aggregate_public_opinion(
        [corrected],
        classifications={corrected.content_id: classification},
        actual_date=date(2026, 7, 20),
        platform="eastmoney",
        code="000001",
        collection_complete=True,
        rules_version="public-opinion-deepseek-v1",
    )
    save_public_opinion_day(
        path,
        contents=[corrected],
        classifications={corrected.content_id: classification},
        aggregate=model_aggregate,
        collected_at=corrected.collected_at,
    )

    import hashlib
    import sqlite3

    with sqlite3.connect(path) as connection:
        stored = connection.execute(
            """
            SELECT likes, content_sha256, sentiment, classification_model,
                   classification_confidence, prompt_version
            FROM public_opinion_content_references
            """
        ).fetchone()
    assert stored == (
        3,
        hashlib.sha256(corrected.text.encode("utf-8")).hexdigest(),
        "favorable",
        "deepseek-v4-pro",
        0.91,
        "public-opinion-deepseek-v1",
    )
