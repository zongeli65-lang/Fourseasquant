from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from fourseasquant.industry_chain.announcement_triage import triage_announcements
from fourseasquant.industry_chain.discovery import DiscoveryItem
from fourseasquant.industry_chain.ollama_runtime import OllamaStructuredRuntime


BEIJING = ZoneInfo("Asia/Shanghai")


def _item(discovery_id: str, headline: str) -> DiscoveryItem:
    now = datetime(2026, 7, 26, 12, 0, tzinfo=BEIJING)
    return DiscoveryItem(
        discovery_id=discovery_id,
        source_id="cninfo",
        external_id=discovery_id,
        security_code="600001",
        security_name="示例股份",
        headline=headline,
        published_at=now,
        collected_at=now,
        source_url="https://www.cninfo.com.cn/example",
        attachment_url=None,
        payload={"headline": headline},
    )


def test_triage_uses_structured_local_model_and_preserves_input_scope() -> None:
    captured: dict[str, object] = {}

    def transport(
        _url: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        captured.update(payload)
        return {
            "model": "qwen3:14b",
            "message": {
                "content": json.dumps(
                    {
                        "decisions": [
                            {
                                "discovery_id": "cninfo:1",
                                "action": "investigate",
                                "suspected_event_type": "major_order",
                                "priority": 1,
                                "reason": "标题明确披露重大订单",
                            },
                            {
                                "discovery_id": "cninfo:2",
                                "action": "ignore",
                                "suspected_event_type": "unrelated",
                                "priority": 3,
                                "reason": "人员任免与供需无关",
                            },
                        ]
                    },
                    ensure_ascii=False,
                )
            },
            "eval_count": 80,
        }

    result = triage_announcements(
        (
            _item("cninfo:1", "关于签订重大订单的公告"),
            _item("cninfo:2", "关于聘任董事会秘书的公告"),
        ),
        runtime=OllamaStructuredRuntime(transport=transport),
    )

    assert result.investigate_ids == ("cninfo:1",)
    assert captured["model"] == "qwen3:14b"
    assert captured["stream"] is False
    assert captured["think"] is False
    assert captured["format"]


def test_triage_rejects_missing_or_invented_discovery_ids() -> None:
    runtime = OllamaStructuredRuntime(
        transport=lambda _url, _payload: {
            "model": "qwen3:14b",
            "message": {
                "content": json.dumps(
                    {
                        "decisions": [
                            {
                                "discovery_id": "invented",
                                "action": "ignore",
                                "suspected_event_type": "unrelated",
                                "priority": 3,
                                "reason": "虚构编号",
                            }
                        ]
                    }
                )
            },
        }
    )

    with pytest.raises(ValueError, match="编号不完整"):
        triage_announcements(
            (_item("cninfo:1", "重大订单"),),
            runtime=runtime,
        )


def test_triage_normalizes_model_action_event_type_conflict() -> None:
    runtime = OllamaStructuredRuntime(
        transport=lambda _url, _payload: {
            "model": "qwen3:14b",
            "message": {
                "content": json.dumps(
                    {
                        "decisions": [
                            {
                                "discovery_id": "cninfo:1",
                                "action": "investigate",
                                "suspected_event_type": "policy_watch",
                                "priority": 2,
                                "reason": "只有政策表述",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            },
        }
    )

    result = triage_announcements(
        (_item("cninfo:1", "发布产业支持政策"),),
        runtime=runtime,
    )

    assert result.batch.decisions[0].action == "observe"
    assert result.batch.decisions[0].suspected_event_type == "policy_watch"
