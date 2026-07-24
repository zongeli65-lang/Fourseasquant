from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from fourseasquant.database import database_path, initialize_database  # noqa: E402
from fourseasquant.discussion_sentiment import (  # noqa: E402
    DiscussionPlatform,
    DiscussionPost,
    aggregate_platform_discussion,
    combine_platform_aggregates,
)
from fourseasquant.fundamental_repository import (  # noqa: E402
    read_platform_discussion_aggregate,
    save_combined_discussion_signal,
    save_discussion_day,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="机械聚合东方财富股吧或雪球单日讨论"
    )
    parser.add_argument("input", type=Path, help="输入 JSON 文件")
    parser.add_argument("--database", type=Path, default=None)
    arguments = parser.parse_args()
    raw = cast(dict[str, object], json.loads(arguments.input.read_text()))
    post_items = cast(list[object], raw["posts"])
    heat_universe = cast(list[float], raw["heat_universe"])
    posts = [DiscussionPost.model_validate(item) for item in post_items]
    aggregate = aggregate_platform_discussion(
        posts,
        heat_universe=heat_universe,
    )
    path = arguments.database or database_path()
    initialize_database(path)
    save_discussion_day(
        path,
        posts=posts,
        aggregate=aggregate,
        created_at=datetime.now(ZoneInfo("Asia/Shanghai")),
    )
    other_platform: DiscussionPlatform = (
        "xueqiu"
        if aggregate.platform == "eastmoney_guba"
        else "eastmoney_guba"
    )
    other = read_platform_discussion_aggregate(
        path,
        platform=other_platform,
        actual_date=aggregate.actual_date,
        code=aggregate.code,
    )
    eastmoney = aggregate if aggregate.platform == "eastmoney_guba" else other
    xueqiu = aggregate if aggregate.platform == "xueqiu" else other
    combined = combine_platform_aggregates(eastmoney, xueqiu)
    if combined is not None:
        save_combined_discussion_signal(
            path,
            combined,
            created_at=datetime.now(ZoneInfo("Asia/Shanghai")),
        )
    print(
        json.dumps(
            {
                "platform": aggregate.model_dump(mode="json"),
                "combined": (
                    combined.model_dump(mode="json")
                    if combined is not None
                    else None
                ),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
