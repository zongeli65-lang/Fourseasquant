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
    DiscussionPost,
    aggregate_platform_discussion,
)
from fourseasquant.fundamental_repository import save_discussion_day  # noqa: E402


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
    print(aggregate.model_dump_json(), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
