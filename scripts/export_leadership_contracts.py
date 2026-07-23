from __future__ import annotations

import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from fourseasquant.sector_leadership import (  # noqa: E402
    election_json_schema,
    membership_json_schema,
)


def main() -> int:
    contract_directory = REPOSITORY_ROOT / "contracts"
    contract_directory.mkdir(parents=True, exist_ok=True)
    outputs = {
        "sector-membership.schema.json": membership_json_schema(),
        "sector-leadership.schema.json": election_json_schema(),
    }
    for filename, schema in outputs.items():
        (contract_directory / filename).write_text(
            json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
