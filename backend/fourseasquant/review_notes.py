from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, RootModel, StringConstraints, model_validator


NormalizedTagText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=40),
]


class ReviewTag(RootModel[NormalizedTagText]):
    pass


class ReviewWriteRequest(BaseModel):
    note: str = Field(max_length=20_000)
    tags: list[ReviewTag] = Field(max_length=30)

    @model_validator(mode="after")
    def remove_duplicate_tags(self) -> ReviewWriteRequest:
        normalized: list[ReviewTag] = []
        seen: set[str] = set()
        for tag in self.tags:
            if tag.root not in seen:
                seen.add(tag.root)
                normalized.append(tag)
        self.tags = normalized
        return self

    def tag_values(self) -> list[str]:
        return [tag.root for tag in self.tags]


class ReviewResponse(BaseModel):
    date: date
    note: str
    tags: list[str]
    updated_at: datetime | None


def read_review(path: Path, review_date: date) -> ReviewResponse:
    with sqlite3.connect(path) as connection:
        row = cast(
            tuple[str, str, str] | None,
            connection.execute(
                """
                SELECT note, tags_json, updated_at
                FROM daily_reviews
                WHERE review_date = ?
                """,
                (review_date.isoformat(),),
            ).fetchone(),
        )
    if row is None:
        return ReviewResponse(
            date=review_date,
            note="",
            tags=[],
            updated_at=None,
        )
    note, tags_json, updated_at = row
    return ReviewResponse(
        date=review_date,
        note=note,
        tags=json.loads(tags_json),
        updated_at=datetime.fromisoformat(updated_at),
    )


def save_review(
    path: Path,
    review_date: date,
    request: ReviewWriteRequest,
) -> ReviewResponse:
    updated_at = datetime.now(ZoneInfo("Asia/Shanghai"))
    tag_values = request.tag_values()
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO daily_reviews (review_date, note, tags_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(review_date) DO UPDATE SET
                note = excluded.note,
                tags_json = excluded.tags_json,
                updated_at = excluded.updated_at
            """,
            (
                review_date.isoformat(),
                request.note,
                json.dumps(tag_values, ensure_ascii=False),
                updated_at.isoformat(),
            ),
        )
    return ReviewResponse(
        date=review_date,
        note=request.note,
        tags=tag_values,
        updated_at=updated_at,
    )
