from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


_LOGGER_NAME = "fourseasquant.tasks"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = record.msg if isinstance(record.msg, dict) else {"message": str(record.msg)}
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def task_log_path() -> Path:
    configured = os.environ.get("FOURSEASQUANT_LOG_PATH")
    return Path(configured) if configured else Path("logs/tasks.jsonl")


def _logger() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    path = task_log_path().resolve()
    if any(getattr(handler, "baseFilename", None) == str(path) for handler in logger.handlers):
        return logger
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path,
        maxBytes=1_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def log_task_event(
    *,
    event: str,
    task_id: int,
    target_date: date,
    stage: str,
    occurred_at: datetime,
    trigger_method: str,
    error_type: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "event": event,
        "task_id": task_id,
        "target_date": target_date.isoformat(),
        "stage": stage,
        "occurred_at": occurred_at.isoformat(),
        "trigger_method": trigger_method,
    }
    if error_type:
        payload["error_type"] = error_type
    _logger().info(payload)
