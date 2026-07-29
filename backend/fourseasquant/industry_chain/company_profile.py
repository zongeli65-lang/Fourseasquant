from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from typing import Any

import akshare as ak  # type: ignore[import-untyped]

from .company_evidence import LocalCompanyEvidence
from .discovery import DiscoveryItem


CNINFO_PROFILE_URL = "https://webapi.cninfo.com.cn/api/sysapi/p_sysapi1133"
EASTMONEY_BUSINESS_URL = (
    "https://emweb.securities.eastmoney.com/PC_HSF10/"
    "BusinessAnalysis/PageAjax"
)
ProfileFetcher = Callable[[str], Any]
BusinessFetcher = Callable[[str], Any]


def collect_cninfo_company_profiles(
    companies: tuple[LocalCompanyEvidence, ...],
    *,
    as_of_time: datetime,
    observed_at: datetime,
    fetch_profile: ProfileFetcher | None = None,
    fetch_business_segments: BusinessFetcher | None = None,
) -> tuple[DiscoveryItem, ...]:
    """补取公司概况，并为所有调查公司核验主营构成占比。"""
    if as_of_time.tzinfo is None or as_of_time.utcoffset() is None:
        raise ValueError("as_of_time 必须包含时区")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at 必须包含时区")
    # 公司概况是当前站立资料，不能拿今天看到的资料回填历史时点。
    if observed_at.date() != as_of_time.date():
        return ()
    fetch = fetch_profile or ak.stock_profile_cninfo
    fetch_segments = fetch_business_segments or ak.stock_zygc_em
    if not companies:
        return ()

    def collect_one(
        company: LocalCompanyEvidence,
    ) -> tuple[DiscoveryItem, ...]:
        items: list[DiscoveryItem] = []
        if company.main_business_name is None:
            profile_item = _company_profile_item(
                company,
                as_of_time=as_of_time,
                observed_at=observed_at,
                fetch=fetch,
            )
            if profile_item is not None:
                items.append(profile_item)
        segment = _business_segment_item(
            company,
            as_of_time=as_of_time,
            observed_at=observed_at,
            fetch=fetch_segments,
        )
        if segment is not None:
            items.append(segment)
        return tuple(items)

    # AKShare 的部分上游会初始化内嵌 JavaScript 引擎；同进程线程并发
    # 会触发原生崩溃，因此这里必须保持串行。
    groups = tuple(collect_one(company) for company in companies)
    return tuple(item for group in groups for item in group)


def _company_profile_item(
    company: LocalCompanyEvidence,
    *,
    as_of_time: datetime,
    observed_at: datetime,
    fetch: ProfileFetcher,
) -> DiscoveryItem | None:
    main_business = ""
    business_scope = ""
    disclosed_name = company.name
    try:
        frame = fetch(company.code)
        if frame is not None and not frame.empty:
            row = frame.iloc[0]
            main_business = str(row.get("主营业务", "")).strip()
            business_scope = str(row.get("经营范围", "")).strip()
            disclosed_name = str(
                row.get("A股简称", row.get("公司名称", company.name))
            ).strip()
    except Exception:
        return None
    if not main_business:
        return None
    profile_text = "；".join(
        part
        for part in (
            f"{company.name}主营业务：{main_business}",
            f"经营范围：{business_scope}" if business_scope else "",
        )
        if part
    )
    digest = hashlib.sha256(
        f"{company.code}|{profile_text}".encode()
    ).hexdigest()
    return DiscoveryItem(
        discovery_id=f"cninfo:profile:{company.code}:{digest[:16]}",
        source_id="cninfo",
        external_id=f"profile:{company.code}:{digest}",
        security_code=company.code,
        security_name=disclosed_name or company.name,
        headline=f"{company.name}公司概况：主营业务",
        published_at=as_of_time,
        collected_at=observed_at,
        source_url=f"{CNINFO_PROFILE_URL}?scode={company.code}",
        attachment_url=None,
        payload={
            "content": profile_text,
            "main_business": main_business,
            "business_scope": business_scope,
            "evidence_kind": "standing_company_profile",
            "profile_provider": "cninfo",
            "profile_observed_at": observed_at.isoformat(),
            "published_at_known": False,
            "publication_time_known": False,
            "research_company_profile": True,
        },
    )


def _business_segment_item(
    company: LocalCompanyEvidence,
    *,
    as_of_time: datetime,
    observed_at: datetime,
    fetch: BusinessFetcher,
) -> DiscoveryItem | None:
    prefix = "SH" if company.code.startswith(("6", "68")) else "SZ"
    try:
        frame = fetch(f"{prefix}{company.code}")
        if frame is None or frame.empty:
            return None
        eligible = frame[
            frame["报告日期"].apply(
                lambda value: value is not None and value <= as_of_time.date()
            )
        ]
        if eligible.empty:
            return None
        latest_date = max(eligible["报告日期"].tolist())
        latest = eligible[eligible["报告日期"] == latest_date]
        products = latest[latest["分类类型"] == "按产品分类"]
        selected = products if not products.empty else latest[
            latest["分类类型"] == "按行业分类"
        ]
    except Exception:
        return None
    rows: list[dict[str, object]] = []
    for _, row in selected.iterrows():
        name = str(row.get("主营构成", "")).strip()
        if not name:
            continue
        rows.append(
            {
                "name": name,
                "revenue_share": _optional_float(row.get("收入比例")),
                "gross_profit_share": _optional_float(row.get("利润比例")),
                "gross_margin": _optional_float(row.get("毛利率")),
            }
        )
    if not rows:
        return None
    rows.sort(
        key=lambda row: _optional_float(
            row["gross_profit_share"]
        ) or 0.0,
        reverse=True,
    )
    principal = rows[0]
    segment_text = "；".join(
        (
            f"{row['name']}（收入占比{_percent(row['revenue_share'])}，"
            f"主营利润占比{_percent(row['gross_profit_share'])}）"
        )
        for row in rows
    )
    content = (
        f"{company.name}{latest_date.isoformat()}主营构成：{segment_text}"
    )
    digest = hashlib.sha256(
        f"{company.code}|{latest_date.isoformat()}|{content}".encode()
    ).hexdigest()
    return DiscoveryItem(
        discovery_id=(
            f"eastmoney-discovery:business:{company.code}:{digest[:16]}"
        ),
        source_id="eastmoney-discovery",
        external_id=f"business:{company.code}:{digest}",
        security_code=company.code,
        security_name=company.name,
        headline=f"{company.name}主营构成：{principal['name']}",
        published_at=datetime.combine(
            latest_date,
            datetime.max.time(),
            tzinfo=as_of_time.tzinfo,
        ),
        collected_at=observed_at,
        source_url=f"{EASTMONEY_BUSINESS_URL}?code={prefix}{company.code}",
        attachment_url=None,
        payload={
            "content": content,
            "business_segments": rows,
            "report_date": latest_date.isoformat(),
            "main_business": principal["name"],
            "revenue_share": principal["revenue_share"],
            "gross_profit_share": principal["gross_profit_share"],
            "evidence_kind": "reported_business_segments",
            "profile_provider": "eastmoney_business_analysis",
            "profile_observed_at": observed_at.isoformat(),
            "published_at_known": True,
            "publication_time_known": False,
            "publication_date_known": True,
            "research_company_profile": True,
        },
    )


def _optional_float(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if result != result:
        return None
    return result


def _percent(value: object) -> str:
    number = _optional_float(value)
    return "未披露" if number is None else f"{number:.1%}"
