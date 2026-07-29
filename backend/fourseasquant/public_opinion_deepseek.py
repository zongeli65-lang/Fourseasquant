from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import cast

import httpx
from pydantic import BaseModel, ConfigDict, Field

from fourseasquant.public_opinion import (
    ModelOpinionClassification,
    ModelOpinionLabel,
    OpinionContent,
)


DEEPSEEK_CHAT_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-pro"
PROMPT_VERSION = "public-opinion-deepseek-v1"
MAX_BATCH_SIZE = 50

class DeepSeekOpinionError(RuntimeError):
    """DeepSeek 舆论分类不可用或返回了不可信结果。"""


class _RawClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_id: str = Field(min_length=1)
    label: ModelOpinionLabel
    confidence: float = Field(ge=0, le=1)


class _RawBatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classifications: list[_RawClassification]


class DeepSeekOpinionClassifier:
    def __init__(
        self,
        *,
        api_key: str,
        client: httpx.Client,
        model: str = DEEPSEEK_MODEL,
        wait: Callable[[float], None] = time.sleep,
        max_attempts: int = 3,
    ) -> None:
        if not api_key.strip():
            raise DeepSeekOpinionError("未配置 DEEPSEEK_API_KEY")
        if max_attempts < 1:
            raise ValueError("DeepSeek 最大尝试次数必须大于零")
        self._api_key = api_key
        self._client = client
        self._model = model
        self._wait = wait
        self._max_attempts = max_attempts

    def classify(
        self,
        contents: list[OpinionContent],
    ) -> dict[str, ModelOpinionClassification]:
        if not contents:
            return {}
        if len(contents) > MAX_BATCH_SIZE:
            raise ValueError(f"单批最多分类 {MAX_BATCH_SIZE} 条舆论内容")
        expected_ids = [content.content_id for content in contents]
        if len(set(expected_ids)) != len(expected_ids):
            raise ValueError("单批舆论内容编号不能重复")
        request = {
            "model": self._model,
            "stream": False,
            "thinking": {"type": "disabled"},
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "max_tokens": 4_096,
            "messages": [
                {
                    "role": "system",
                    "content": _SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "items": [
                                {
                                    "content_id": content.content_id,
                                    "code": content.code,
                                    "text": content.text,
                                }
                                for content in contents
                            ]
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
        }
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                raw, response_model = self._request_once(request)
                break
            except Exception as error:
                last_error = error
                if not _is_retryable(error) or attempt >= self._max_attempts:
                    raise DeepSeekOpinionError(
                        "DeepSeek 舆论分类失败："
                        f"{type(error).__name__}: {error}"
                    ) from error
                self._wait(float(2 ** (attempt - 1)))
        else:
            assert last_error is not None
            raise DeepSeekOpinionError(
                "DeepSeek 舆论分类失败：已用尽重试次数"
            ) from last_error
        received_ids = [item.content_id for item in raw.classifications]
        if len(set(received_ids)) != len(received_ids):
            raise DeepSeekOpinionError("DeepSeek 返回了重复的内容编号")
        if set(received_ids) != set(expected_ids):
            raise DeepSeekOpinionError("DeepSeek 返回的内容编号与请求不一致")
        return {
            item.content_id: ModelOpinionClassification(
                content_id=item.content_id,
                label=item.label,
                confidence=item.confidence,
                model=response_model,
                prompt_version=PROMPT_VERSION,
            )
            for item in raw.classifications
        }

    def _request_once(
        self,
        request: dict[str, object],
    ) -> tuple[_RawBatchResponse, str]:
        response = self._client.post(
            DEEPSEEK_CHAT_URL,
            json=request,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        payload = cast(dict[str, object], response.json())
        choices = cast(list[object], payload["choices"])
        first = cast(dict[str, object], choices[0])
        message = cast(dict[str, object], first["message"])
        content = cast(str, message["content"])
        return (
            _RawBatchResponse.model_validate_json(content),
            str(payload.get("model") or self._model),
        )


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return (
            error.response.status_code == 429
            or error.response.status_code >= 500
        )
    return isinstance(
        error,
        (
            httpx.TransportError,
            json.JSONDecodeError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
        ),
    )


_SYSTEM_PROMPT = """
你是中国 A 股公开讨论分类器。只判断每条文本对指定股票的态度，不提供投资建议。
必须返回 JSON，格式示例：
{"classifications":[
  {"content_id":"1","label":"favorable","confidence":0.95}
]}
label 只能是：
- favorable：明确看多、预期上涨、正面评价或正向事件；
- unfavorable：明确看空、预期下跌、负面评价或负向事件；
- disputed：同一文本同时有清晰的正反态度；
- neutral：与该股票有关，但没有明确方向；
- unrelated：与该股票无关或无法形成语义。
必须逐条返回且保持 content_id 原样；不得增加解释字段。
""".strip()
