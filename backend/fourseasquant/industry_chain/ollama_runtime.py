from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import cast
from urllib.request import Request, urlopen


OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
PRODUCTION_MODEL = "qwen3:14b"
JsonTransport = Callable[[str, Mapping[str, object]], Mapping[str, object]]


class LocalModelUnavailable(RuntimeError):
    """本机模型服务不可访问或没有返回有效结果。"""


@dataclass(frozen=True)
class StructuredModelResult:
    payload: dict[str, object]
    model: str
    total_duration_ns: int | None
    load_duration_ns: int | None
    prompt_eval_count: int | None
    eval_count: int | None


class OllamaStructuredRuntime:
    """只连接本机回环地址的 Ollama 结构化推理适配器。"""

    def __init__(
        self,
        *,
        model: str = PRODUCTION_MODEL,
        transport: JsonTransport | None = None,
    ) -> None:
        self._model = model
        self._transport = transport or _post_json

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        output_schema: Mapping[str, object],
    ) -> StructuredModelResult:
        request: dict[str, object] = {
            "model": self._model,
            "stream": False,
            "think": False,
            "keep_alive": "5m",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "format": dict(output_schema),
            "options": {
                "temperature": 0,
                "num_ctx": 8192,
            },
        }
        try:
            response = self._transport(OLLAMA_CHAT_URL, request)
            message = cast(Mapping[str, object], response["message"])
            content = cast(str, message["content"])
            payload = cast(dict[str, object], json.loads(content))
        except Exception as error:
            raise LocalModelUnavailable(
                f"本地模型结构化调用失败：{type(error).__name__}: {error}"
            ) from error
        return StructuredModelResult(
            payload=payload,
            model=cast(str, response.get("model") or self._model),
            total_duration_ns=_optional_int(response.get("total_duration")),
            load_duration_ns=_optional_int(response.get("load_duration")),
            prompt_eval_count=_optional_int(response.get("prompt_eval_count")),
            eval_count=_optional_int(response.get("eval_count")),
        )


def _post_json(
    url: str,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=1_200) as response:
        body = response.read(10_000_000)
    return cast(Mapping[str, object], json.loads(body))


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None
