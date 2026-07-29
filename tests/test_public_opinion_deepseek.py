import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from fourseasquant.public_opinion import ModelOpinionClassification, OpinionContent
from fourseasquant.public_opinion_deepseek import (
    DeepSeekOpinionClassifier,
    DeepSeekOpinionError,
)


BEIJING = ZoneInfo("Asia/Shanghai")


def test_deepseek_pro_classifies_public_opinion_batch_as_json() -> None:
    captured_request: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_request.update(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer test-secret"
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-pro",
                "choices": [{
                    "message": {
                        "content": (
                            '{"classifications":['
                            '{"content_id":"topic-1","label":"favorable",'
                            '"confidence":0.96},'
                            '{"content_id":"topic-2","label":"unrelated",'
                            '"confidence":0.91}]}'
                        )
                    }
                }],
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 30,
                },
            },
        )

    contents = [
        _content("topic-1", "满仓茅台，继续看涨"),
        _content("topic-2", "今天中午吃什么"),
    ]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = DeepSeekOpinionClassifier(
            api_key="test-secret",
            client=client,
        ).classify(contents)

    assert captured_request["model"] == "deepseek-v4-pro"
    assert captured_request["thinking"] == {"type": "disabled"}
    assert captured_request["response_format"] == {"type": "json_object"}
    assert result["topic-1"].label == "favorable"
    assert result["topic-1"].confidence == 0.96
    assert result["topic-2"].label == "unrelated"
    assert result["topic-2"].model == "deepseek-v4-pro"
    assert result["topic-2"].prompt_version == "public-opinion-deepseek-v1"


def test_deepseek_failure_returns_no_partial_classification() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="temporarily unavailable")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        classifier = DeepSeekOpinionClassifier(
            api_key="test-secret",
            client=client,
            wait=lambda _: None,
        )
        with pytest.raises(DeepSeekOpinionError, match="DeepSeek 舆论分类失败"):
            classifier.classify([_content("topic-1", "继续看涨")])


def test_deepseek_retries_transient_failure_as_one_atomic_batch() -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, text="temporarily unavailable")
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-pro",
                "choices": [{
                    "message": {
                        "content": (
                            '{"classifications":[{"content_id":"topic-1",'
                            '"label":"favorable","confidence":0.9}]}'
                        )
                    }
                }],
            },
        )

    waits: list[float] = []
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = DeepSeekOpinionClassifier(
            api_key="test-secret",
            client=client,
            wait=waits.append,
        ).classify([_content("topic-1", "继续看涨")])

    assert attempts == 2
    assert waits == [1.0]
    assert result["topic-1"].label == "favorable"


def _content(content_id: str, text: str) -> OpinionContent:
    now = datetime(2026, 7, 27, 14, tzinfo=BEIJING)
    return OpinionContent(
        platform="sina",
        content_id=content_id,
        code="600519",
        url=f"https://example.test/{content_id}",
        published_at=now,
        collected_at=now,
        likes=0,
        content_kind="topic",
        source_type="user_original",
        text=text,
    )
