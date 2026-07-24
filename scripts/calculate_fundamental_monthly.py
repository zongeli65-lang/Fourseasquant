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
from fourseasquant.fundamental_mechanical import (  # noqa: E402
    MonthlyFundamentalInput,
    calculate_monthly_snapshot,
)
from fourseasquant.fundamental_repository import save_monthly_snapshot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="根据结构化真实数据计算个人基本面月度快照"
    )
    parser.add_argument("input", type=Path, help="输入 JSON 文件")
    parser.add_argument("--database", type=Path, default=None)
    arguments = parser.parse_args()
    raw = cast(dict[str, object], json.loads(arguments.input.read_text()))
    source_urls = cast(list[str], raw.pop("source_urls", []))
    source = MonthlyFundamentalInput.model_validate(raw)
    snapshot = calculate_monthly_snapshot(source)
    path = arguments.database or database_path()
    initialize_database(path)
    saved = save_monthly_snapshot(
        path,
        snapshot,
        source_urls=source_urls,
        created_at=datetime.now(ZoneInfo("Asia/Shanghai")),
    )
    print(
        json.dumps(
            {
                "stored": saved.stored,
                "changed_fields": saved.changed_fields,
                "snapshot": snapshot.model_dump(mode="json"),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
