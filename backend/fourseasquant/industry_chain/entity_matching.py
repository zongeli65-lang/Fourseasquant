from __future__ import annotations

import re


_CJK_OR_ALNUM = re.compile(r"[\u3400-\u9fffA-Za-z0-9]")
_SHORT_NAME_CONTEXT_SUFFIXES = (
    "公司",
    "集团",
    "股份",
    "证券",
    "银行",
    "科技",
    "材料",
    "能源",
    "公告",
    "主营",
)


def company_name_mentioned(name: str, text: str) -> bool:
    """保守识别证券简称，避免把短简称嵌在另一实体名称中。"""
    normalized_name = name.strip()
    if len(normalized_name) < 2:
        return False
    start = 0
    while True:
        index = text.find(normalized_name, start)
        if index < 0:
            return False
        end = index + len(normalized_name)
        if len(normalized_name) >= 4:
            return True
        previous = text[index - 1] if index > 0 else ""
        following = text[end] if end < len(text) else ""
        embedded_on_both_sides = (
            bool(previous and _CJK_OR_ALNUM.fullmatch(previous))
            and bool(following and _CJK_OR_ALNUM.fullmatch(following))
        )
        allowed_suffix = text[end:].startswith(_SHORT_NAME_CONTEXT_SUFFIXES)
        if not embedded_on_both_sides or allowed_suffix:
            return True
        start = index + 1


def company_name_mention_count(name: str, text: str) -> int:
    """返回保守匹配次数，供召回排序使用。"""
    count = 0
    start = 0
    while start < len(text):
        index = text.find(name, start)
        if index < 0:
            break
        if company_name_mentioned(name, text[index:]):
            count += 1
        start = index + max(1, len(name))
    return count
