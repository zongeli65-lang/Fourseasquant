from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import BaseModel, Field, model_validator

from fourseasquant.fundamental_mechanical import ShareholderActions


BEIJING = ZoneInfo("Asia/Shanghai")
CNINFO_MANAGEMENT_SOURCE_URL = (
    "https://webapi.cninfo.com.cn/#/thematicStatistics"
)
NON_CASH_REASON_KEYWORDS = (
    "股权激励",
    "期权",
    "行权",
    "继承",
    "司法",
    "赠与",
    "分红",
    "送股",
    "转增",
    "非交易过户",
    "可转债",
)
ACTIVE_CASH_REASON_KEYWORDS = (
    "竞价交易",
    "集中竞价",
    "二级市场",
    "大宗交易",
    "协议转让",
)
DILUTION_REASON_KEYWORDS = (
    "增发",
    "配股",
    "可转债转股",
    "债转股",
)
CNINFO_SHARE_CHANGE_SOURCE_URL = "https://webapi.cninfo.com.cn/#/apiDoc"
EASTMONEY_REPURCHASE_SOURCE_URL = "https://data.eastmoney.com/gphg/hglist.html"
CANCELLATION_COMPLETION_PHRASES = (
    "回购股份注销完成",
    "完成回购股份注销",
    "回购注销完成",
)


class CapitalActionEventType(str, Enum):
    insider_buy = "insider_buy"
    insider_sell = "insider_sell"
    cancelled_buyback = "cancelled_buyback"
    share_dilution = "share_dilution"
    buyback_announcement = "buyback_announcement"


class CapitalActionEvent(BaseModel):
    event_key: str = Field(min_length=64, max_length=64)
    code: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1)
    event_type: CapitalActionEventType
    announcement_at: datetime
    effective_date: date
    shares: float | None = Field(default=None, ge=0)
    amount_cny: float | None = Field(default=None, ge=0)
    price_cny: float | None = Field(default=None, ge=0)
    shares_before: float | None = Field(default=None, gt=0)
    reason: str | None = None
    confirmed_for_score: bool
    exclusion_reason: str | None = None
    source_name: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)
    content_sha256: str = Field(min_length=64, max_length=64)
    raw_payload: dict[str, object] = Field(default_factory=dict)
    collected_at: datetime


class CapitalActionSnapshot(BaseModel):
    as_of_date: date
    insider_window_complete: bool = True
    expected_codes: list[str]
    completed_codes: list[str]
    events: list[CapitalActionEvent]
    errors: dict[str, str]

    @model_validator(mode="after")
    def validate_coverage(self) -> CapitalActionSnapshot:
        self.expected_codes = sorted(set(self.expected_codes))
        self.completed_codes = sorted(set(self.completed_codes))
        expected = set(self.expected_codes)
        completed = set(self.completed_codes)
        if not completed.issubset(expected):
            raise ValueError("已完成股票必须属于预期股票集合")
        error_codes = set(self.errors) - {"__global__"}
        if not error_codes.issubset(expected):
            raise ValueError("失败股票必须属于预期股票集合")
        if any(event.code not in completed for event in self.events):
            raise ValueError("资本事件只能属于已完成采集的股票")
        return self

    @property
    def complete(self) -> bool:
        return (
            bool(self.expected_codes)
            and set(self.completed_codes) == set(self.expected_codes)
            and not self.errors
        )


class CapitalActionSnapshotSaveResult(BaseModel):
    batch_id: int
    status: Literal["published", "failed"]
    published: bool


class PublishedCapitalActionSnapshot(CapitalActionSnapshot):
    batch_id: int
    published_at: datetime


class CapitalActionBatchRun(CapitalActionSnapshot):
    batch_id: int
    status: Literal["published", "failed"]
    collected_at: datetime
    published_at: datetime | None


def collect_capital_action_snapshot(
    *,
    codes: list[str],
    as_of_date: date,
    collected_at: datetime,
    management_increases: pd.DataFrame,
    management_decreases: pd.DataFrame,
    repurchases: pd.DataFrame,
    share_change_fetcher: Callable[[str], pd.DataFrame],
    announcement_fetcher: Callable[[str], pd.DataFrame],
    management_coverage_complete: bool = True,
    max_workers: int = 1,
) -> CapitalActionSnapshot:
    expected_codes = sorted({str(code).zfill(6) for code in codes})
    window_start = _one_year_before(as_of_date)
    management_events = normalize_management_change_frames(
        increases=management_increases,
        decreases=management_decreases,
        collected_at=collected_at,
    )
    if not management_coverage_complete:
        management_events = [
            event.model_copy(
                update={
                    "confirmed_for_score": False,
                    "exclusion_reason": (
                        "高管增减持接口无法证明目标日前十二个月窗口完整"
                    ),
                }
            )
            for event in management_events
        ]
    management_by_code: dict[str, list[CapitalActionEvent]] = {}
    for event in management_events:
        management_by_code.setdefault(event.code, []).append(event)

    def collect_code(code: str) -> list[CapitalActionEvent]:
        code_events = [
            *management_by_code.get(code, []),
            *normalize_share_change_frame(
                frame=share_change_fetcher(code),
                collected_at=collected_at,
            ),
        ]
        code_repurchases = repurchases[
            repurchases["股票代码"].astype(str).str.zfill(6).eq(code)
        ]
        if not code_repurchases.empty:
            code_events.extend(
                normalize_cancelled_buybacks(
                    repurchases=code_repurchases,
                    announcements=announcement_fetcher(code),
                    collected_at=collected_at,
                )
            )
        return [
            event
            for event in code_events
            if event.announcement_at.date() <= as_of_date
            and window_start <= event.effective_date
            and event.effective_date <= as_of_date
        ]

    completed_codes: list[str] = []
    errors: dict[str, str] = {}
    events: list[CapitalActionEvent] = []
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = {
            executor.submit(collect_code, code): code
            for code in expected_codes
        }
        for future in as_completed(futures):
            code = futures[future]
            try:
                events.extend(future.result())
                completed_codes.append(code)
            except Exception as error:
                errors[code] = f"{type(error).__name__}: {error}"
    deduplicated = {
        event.event_key: event
        for event in sorted(
            events,
            key=lambda event: (
                event.code,
                event.announcement_at,
                event.event_key,
            ),
        )
    }
    return CapitalActionSnapshot(
        as_of_date=as_of_date,
        insider_window_complete=management_coverage_complete,
        expected_codes=expected_codes,
        completed_codes=completed_codes,
        events=list(deduplicated.values()),
        errors=errors,
    )


def normalize_management_change_frames(
    *,
    increases: pd.DataFrame,
    decreases: pd.DataFrame,
    collected_at: datetime,
) -> list[CapitalActionEvent]:
    events = [
        *_management_events(
            increases,
            event_type=CapitalActionEventType.insider_buy,
            collected_at=collected_at,
        ),
        *_management_events(
            decreases,
            event_type=CapitalActionEventType.insider_sell,
            collected_at=collected_at,
        ),
    ]
    return sorted(
        events,
        key=lambda event: (
            event.announcement_at,
            event.effective_date,
            event.event_type.value,
            event.event_key,
        ),
    )


def normalize_share_change_frame(
    *,
    frame: pd.DataFrame,
    collected_at: datetime,
) -> list[CapitalActionEvent]:
    required = {
        "证券代码",
        "证券简称",
        "公告日期",
        "变动日期",
        "变动原因",
        "总股本",
        "已流通股份",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"公司股本变动缺少字段: {sorted(missing)}")
    normalized = frame.copy()
    normalized["_变动日期"] = pd.to_datetime(
        normalized["变动日期"],
        errors="coerce",
    )
    normalized = normalized.dropna(subset=["_变动日期"]).sort_values(
        ["_变动日期", "公告日期"],
        kind="stable",
    )
    events: list[CapitalActionEvent] = []
    previous_total_shares: float | None = None
    for row_index, row in normalized.iterrows():
        total_shares = _optional_nonnegative_number(row["总股本"])
        reason = _optional_text(row["变动原因"])
        announcement_date = _optional_date(row["公告日期"])
        if (
            previous_total_shares is not None
            and total_shares is not None
            and total_shares > previous_total_shares
            and reason is not None
            and any(keyword in reason for keyword in DILUTION_REASON_KEYWORDS)
            and announcement_date is not None
        ):
            code = str(row["证券代码"]).zfill(6)
            effective_date = _required_date(row["变动日期"], "变动日期")
            raw_record: dict[str, object] = {
                str(key): _json_value(value)
                for key, value in row.to_dict().items()
                if key != "_变动日期"
            }
            content_sha256 = _content_hash(raw_record)
            source_record_id = _content_hash(
                {
                    "row_index": str(row_index),
                    "code": code,
                    "announcement_date": announcement_date.isoformat(),
                    "effective_date": effective_date.isoformat(),
                    "content_sha256": content_sha256,
                }
            )
            events.append(
                CapitalActionEvent(
                    event_key=_content_hash(
                        {
                            "source_record_id": source_record_id,
                            "event_type": (
                                CapitalActionEventType.share_dilution.value
                            ),
                            "content_sha256": content_sha256,
                        }
                    ),
                    code=code,
                    name=str(row["证券简称"]).strip(),
                    event_type=CapitalActionEventType.share_dilution,
                    announcement_at=datetime.combine(
                        announcement_date,
                        time.min,
                        tzinfo=BEIJING,
                    ),
                    effective_date=effective_date,
                    shares=(total_shares - previous_total_shares) * 10_000,
                    shares_before=previous_total_shares * 10_000,
                    reason=reason,
                    confirmed_for_score=True,
                    source_name="巨潮资讯",
                    source_url=CNINFO_SHARE_CHANGE_SOURCE_URL,
                    source_record_id=source_record_id,
                    content_sha256=content_sha256,
                    raw_payload=raw_record,
                    collected_at=collected_at,
                )
            )
        if total_shares is not None:
            previous_total_shares = total_shares
    return events


def normalize_cancelled_buybacks(
    *,
    repurchases: pd.DataFrame,
    announcements: pd.DataFrame,
    collected_at: datetime,
) -> list[CapitalActionEvent]:
    repurchase_required = {
        "股票代码",
        "股票简称",
        "回购起始时间",
        "实施进度",
        "已回购股份数量",
        "已回购金额",
        "最新公告日期",
    }
    announcement_required = {
        "代码",
        "简称",
        "公告标题",
        "公告时间",
        "公告链接",
    }
    missing_repurchases = repurchase_required.difference(repurchases.columns)
    if missing_repurchases:
        raise ValueError(f"股票回购数据缺少字段: {sorted(missing_repurchases)}")
    missing_announcements = announcement_required.difference(
        announcements.columns
    )
    if missing_announcements:
        raise ValueError(f"回购公告数据缺少字段: {sorted(missing_announcements)}")
    completed = repurchases[repurchases["实施进度"] == "完成实施"]
    events: list[CapitalActionEvent] = []
    consumed_announcements: set[object] = set()
    for row_index, row in completed.iterrows():
        code = str(row["股票代码"]).zfill(6)
        name = str(row["股票简称"]).strip()
        latest_announcement_date = _required_date(
            row["最新公告日期"],
            "最新公告日期",
        )
        reported_amount = _optional_nonnegative_number(row["已回购金额"])
        reported_shares = _optional_nonnegative_number(
            row["已回购股份数量"]
        )
        matches = announcements[
            announcements["代码"].astype(str).str.zfill(6).eq(code)
        ].copy()
        matches["_公告时间"] = pd.to_datetime(
            matches["公告时间"],
            errors="coerce",
        )
        matches = matches[
            matches["_公告时间"].notna()
            & (
                matches["_公告时间"].dt.date
                >= latest_announcement_date
            )
            & (
                matches["_公告时间"].dt.date
                <= latest_announcement_date + timedelta(days=366)
            )
        ].sort_values("_公告时间", kind="stable")
        confirmed_row: pd.Series | None = None
        confirmed_index: object | None = None
        for announcement_index, announcement in matches.iterrows():
            if announcement_index in consumed_announcements:
                continue
            title = (
                str(announcement["公告标题"])
                .replace("<em>", "")
                .replace("</em>", "")
            )
            if any(phrase in title for phrase in CANCELLATION_COMPLETION_PHRASES):
                confirmed_row = announcement
                confirmed_index = announcement_index
                break
        official_amount: float | None = None
        official_shares: float | None = None
        if confirmed_row is not None:
            official_announcement_date = _required_date(
                confirmed_row["公告时间"],
                "公告时间",
            )
            official_document_url = confirmed_row.get("官方文件链接")
            source_url = (
                str(official_document_url).strip()
                if official_document_url is not None
                and not pd.isna(official_document_url)
                and str(official_document_url).strip()
                else str(confirmed_row["公告链接"]).strip()
            )
            effective_date = official_announcement_date
            official_amount = _first_optional_row_number(
                confirmed_row,
                ("注销金额", "回购金额", "已回购金额"),
            )
            official_shares = _first_optional_row_number(
                confirmed_row,
                ("注销股份数量", "回购注销股份数量", "已注销股份数量"),
            )
            if official_amount is None or official_amount == 0:
                confirmed_for_score = False
                exclusion_reason = "官方注销完成公告缺少可核验金额"
            elif official_shares is None or official_shares == 0:
                confirmed_for_score = False
                exclusion_reason = "官方注销完成公告缺少可核验注销股数"
            elif not _approximately_equal(
                official_amount,
                reported_amount,
            ) or not _approximately_equal(
                official_shares,
                reported_shares,
            ):
                confirmed_for_score = False
                exclusion_reason = "官方注销金额或股数无法对应本轮回购"
            else:
                confirmed_for_score = True
                exclusion_reason = None
                assert confirmed_index is not None
                consumed_announcements.add(confirmed_index)
        else:
            source_url = EASTMONEY_REPURCHASE_SOURCE_URL
            confirmed_for_score = False
            exclusion_reason = "缺少官方回购股份注销完成公告"
            effective_date = latest_announcement_date
        event_type = (
            CapitalActionEventType.cancelled_buyback
            if confirmed_for_score
            else CapitalActionEventType.buyback_announcement
        )
        shares = (
            official_shares
            if confirmed_for_score
            else reported_shares
        )
        amount_cny = (
            official_amount
            if confirmed_for_score
            else reported_amount
        )
        raw_record: dict[str, object] = {
            str(key): _json_value(value)
            for key, value in row.to_dict().items()
        }
        if confirmed_row is not None:
            raw_record["官方注销公告"] = {
                str(key): _json_value(value)
                for key, value in confirmed_row.to_dict().items()
            }
        metadata_sha256 = _content_hash(raw_record)
        official_content_sha256 = (
            _optional_text(confirmed_row.get("官方文件哈希"))
            if confirmed_row is not None
            else None
        )
        content_sha256 = official_content_sha256 or metadata_sha256
        source_record_id = _content_hash(
            {
                "row_index": str(row_index),
                "code": code,
                "latest_announcement_date": (
                    latest_announcement_date.isoformat()
                ),
                "content_sha256": content_sha256,
            }
        )
        events.append(
            CapitalActionEvent(
                event_key=_content_hash(
                    {
                        "source_record_id": source_record_id,
                        "event_type": event_type.value,
                        "content_sha256": content_sha256,
                    }
                ),
                code=code,
                name=name,
                event_type=event_type,
                announcement_at=datetime.combine(
                    effective_date,
                    time.min,
                    tzinfo=BEIJING,
                ),
                effective_date=effective_date,
                shares=shares,
                amount_cny=amount_cny,
                reason="完成实施的股票回购",
                confirmed_for_score=confirmed_for_score,
                exclusion_reason=exclusion_reason,
                source_name=(
                    "巨潮资讯＋东方财富"
                    if confirmed_row is not None
                    else "东方财富"
                ),
                source_url=source_url,
                source_record_id=source_record_id,
                content_sha256=content_sha256,
                raw_payload=raw_record,
                collected_at=collected_at,
            )
        )
    return sorted(events, key=lambda event: (event.code, event.effective_date))


def aggregate_shareholder_actions(
    *,
    events: list[CapitalActionEvent],
    as_of_date: date,
    average_floating_market_cap: float,
    insider_window_complete: bool = True,
) -> ShareholderActions:
    start_date = _one_year_before(as_of_date)
    eligible = [
        event
        for event in events
        if event.confirmed_for_score
        and start_date <= event.effective_date <= as_of_date
        and event.announcement_at.date() <= as_of_date
    ]
    insider_net_purchase_amount = sum(
        (
            event.amount_cny or 0
            if event.event_type == CapitalActionEventType.insider_buy
            else -(event.amount_cny or 0)
        )
        for event in eligible
        if event.event_type
        in (
            CapitalActionEventType.insider_buy,
            CapitalActionEventType.insider_sell,
        )
    )
    cancelled_buyback_amount = sum(
        event.amount_cny or 0
        for event in eligible
        if event.event_type == CapitalActionEventType.cancelled_buyback
    )
    dilution_events = sorted(
        (
            event
            for event in eligible
            if event.event_type == CapitalActionEventType.share_dilution
        ),
        key=lambda event: (event.effective_date, event.event_key),
    )
    return ShareholderActions(
        average_floating_market_cap=average_floating_market_cap,
        insider_window_complete=insider_window_complete,
        insider_net_purchase_amount=(
            insider_net_purchase_amount
            if insider_window_complete
            else None
        ),
        cancelled_buyback_amount=cancelled_buyback_amount,
        newly_issued_shares=sum(event.shares or 0 for event in dilution_events),
        shares_before_issuance=(
            dilution_events[0].shares_before if dilution_events else None
        ),
    )


def calculate_average_floating_market_cap(
    *,
    closes: pd.DataFrame,
    share_changes: pd.DataFrame,
    as_of_date: date,
) -> float:
    date_column = _first_column(closes, ("交易日期", "actual_data_date"))
    close_column = _first_column(closes, ("收盘价", "close"))
    share_column = _first_column(
        share_changes,
        ("已流通股份", "人民币普通股"),
    )
    if "变动日期" not in share_changes.columns:
        raise ValueError("股本历史缺少变动日期")
    price_rows = closes.loc[:, [date_column, close_column]].copy()
    price_rows["_日期"] = pd.to_datetime(
        price_rows[date_column],
        errors="coerce",
    ).astype("datetime64[ns]")
    price_rows["_收盘价"] = pd.to_numeric(
        price_rows[close_column],
        errors="coerce",
    )
    start_date = _one_year_before(as_of_date)
    price_rows = price_rows[
        price_rows["_日期"].dt.date.between(start_date, as_of_date)
    ].dropna(subset=["_日期", "_收盘价"])
    share_rows = share_changes.loc[:, ["变动日期", share_column]].copy()
    share_rows["_日期"] = pd.to_datetime(
        share_rows["变动日期"],
        errors="coerce",
    ).astype("datetime64[ns]")
    share_rows["_流通股"] = pd.to_numeric(
        share_rows[share_column],
        errors="coerce",
    )
    share_rows = share_rows.dropna(subset=["_日期", "_流通股"])
    if price_rows.empty or share_rows.empty:
        raise ValueError("缺少计算平均流通市值所需的行情或股本历史")
    matched = pd.merge_asof(
        price_rows.sort_values("_日期"),
        share_rows.sort_values("_日期"),
        on="_日期",
        direction="backward",
    ).dropna(subset=["_流通股"])
    if matched.empty:
        raise ValueError("行情日期之前没有有效流通股本记录")
    values = matched["_收盘价"] * matched["_流通股"] * 10_000
    average = float(values.mean())
    if not math.isfinite(average) or average <= 0:
        raise ValueError("平均流通市值不是有效正数")
    return average


def _first_column(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str:
    for column in candidates:
        if column in frame.columns:
            return column
    raise ValueError(f"数据缺少字段之一: {list(candidates)}")


def _one_year_before(value: date) -> date:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def _management_events(
    frame: pd.DataFrame,
    *,
    event_type: CapitalActionEventType,
    collected_at: datetime,
) -> list[CapitalActionEvent]:
    required = {
        "证券代码",
        "证券简称",
        "截止日期",
        "公告日期",
        "变动数量",
        "成交均价",
        "持股变动原因",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"高管持股变动缺少字段: {sorted(missing)}")
    events: list[CapitalActionEvent] = []
    for row_index, row in frame.iterrows():
        code = str(row["证券代码"]).zfill(6)
        name = str(row["证券简称"]).strip()
        announcement_date = _required_date(row["公告日期"], "公告日期")
        effective_date = _required_date(row["截止日期"], "截止日期")
        shares = _optional_nonnegative_number(row["变动数量"])
        price = _optional_nonnegative_number(row["成交均价"])
        reason = _optional_text(row["持股变动原因"])
        exclusion_reason = _management_exclusion_reason(
            reason=reason,
            shares=shares,
            price=price,
        )
        raw_record: dict[str, object] = {
            str(key): _json_value(value)
            for key, value in row.to_dict().items()
        }
        content_sha256 = _content_hash(raw_record)
        source_record_id = _content_hash(
            {
                "row_index": str(row_index),
                "code": code,
                "event_type": event_type.value,
                "announcement_date": announcement_date.isoformat(),
                "effective_date": effective_date.isoformat(),
                "content_sha256": content_sha256,
            }
        )
        event_payload = {
            "source_record_id": source_record_id,
            "event_type": event_type.value,
            "content_sha256": content_sha256,
        }
        events.append(
            CapitalActionEvent(
                event_key=_content_hash(event_payload),
                code=code,
                name=name,
                event_type=event_type,
                announcement_at=datetime.combine(
                    announcement_date,
                    time.min,
                    tzinfo=BEIJING,
                ),
                effective_date=effective_date,
                shares=shares,
                amount_cny=(
                    shares * price
                    if shares is not None and price is not None
                    else None
                ),
                price_cny=price,
                reason=reason,
                confirmed_for_score=exclusion_reason is None,
                exclusion_reason=exclusion_reason,
                source_name="巨潮资讯",
                source_url=CNINFO_MANAGEMENT_SOURCE_URL,
                source_record_id=source_record_id,
                content_sha256=content_sha256,
                raw_payload=raw_record,
                collected_at=collected_at,
            )
        )
    return events


def _management_exclusion_reason(
    *,
    reason: str | None,
    shares: float | None,
    price: float | None,
) -> str | None:
    if shares is None or shares == 0:
        return "缺少有效变动数量"
    if price is None or price == 0:
        return "缺少有效成交均价"
    if reason is None:
        return "缺少交易原因"
    for keyword in NON_CASH_REASON_KEYWORDS:
        if keyword in reason:
            return f"非主动现金交易：{reason}"
    if not any(keyword in reason for keyword in ACTIVE_CASH_REASON_KEYWORDS):
        return f"无法确认主动现金交易：{reason}"
    return None


def _required_date(value: object, label: str) -> date:
    parsed = pd.to_datetime(str(value), errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"高管持股变动缺少有效{label}")
    return parsed.date()


def _optional_date(value: object) -> date | None:
    parsed = pd.to_datetime(str(value), errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def _first_optional_row_number(
    row: pd.Series,
    columns: tuple[str, ...],
) -> float | None:
    for column in columns:
        if column in row.index:
            value = _optional_nonnegative_number(row[column])
            if value is not None:
                return value
    return None


def _approximately_equal(
    official: float,
    reported: float | None,
    *,
    relative_tolerance: float = 0.01,
) -> bool:
    if reported is None or reported <= 0:
        return False
    return abs(official - reported) <= max(
        1.0,
        abs(reported) * relative_tolerance,
    )


def _optional_nonnegative_number(value: object) -> float | None:
    try:
        number = abs(float(cast(Any, value)))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    if result in {"", "nan", "NaT", "<NA>"}:
        return None
    return result or None


def _content_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_value(value: Any) -> object:
    if value is None or (
        not isinstance(value, (list, dict))
        and bool(pd.isna(cast(Any, value)))
    ):
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value
