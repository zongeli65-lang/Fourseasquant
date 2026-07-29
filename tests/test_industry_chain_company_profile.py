from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from fourseasquant.industry_chain.company_evidence import LocalCompanyEvidence
from fourseasquant.industry_chain.company_profile import (
    collect_cninfo_company_profiles,
)


BEIJING = ZoneInfo("Asia/Shanghai")


def _company() -> LocalCompanyEvidence:
    return LocalCompanyEvidence(
        code="002709",
        name="天赐材料",
        universe_version="universe-v1",
        availability="unavailable",
        evidence_as_of_date=None,
        published_at=None,
        rules_version="personal-fundamental-v1",
        main_business_name=None,
        gross_profit_share=None,
        revenue_share=None,
        source_urls=(),
        ownership_evidence_available=False,
        limitations=("缺少已发布主营快照",),
    )


def test_collect_cninfo_company_profile_creates_official_business_evidence() -> None:
    as_of = datetime(2026, 7, 27, 15, 57, tzinfo=BEIJING)
    observed = datetime(2026, 7, 27, 16, 5, tzinfo=BEIJING)

    items = collect_cninfo_company_profiles(
        (_company(),),
        as_of_time=as_of,
        observed_at=observed,
        fetch_profile=lambda _: pd.DataFrame(
            [
                {
                    "A股简称": "天赐材料",
                    "主营业务": "锂离子电池材料、电解液及日化材料",
                    "经营范围": "精细化工材料研发和生产",
                }
            ]
        ),
        fetch_business_segments=lambda _: pd.DataFrame(),
    )

    assert len(items) == 1
    assert items[0].security_code == "002709"
    assert items[0].source_id == "cninfo"
    assert items[0].payload["evidence_kind"] == "standing_company_profile"
    assert "电解液" in str(items[0].payload["main_business"])


def test_current_company_profile_cannot_backfill_historical_day() -> None:
    items = collect_cninfo_company_profiles(
        (_company(),),
        as_of_time=datetime(2026, 7, 26, 15, 57, tzinfo=BEIJING),
        observed_at=datetime(2026, 7, 27, 16, 5, tzinfo=BEIJING),
        fetch_profile=lambda _: pd.DataFrame(),
        fetch_business_segments=lambda _: pd.DataFrame(),
    )

    assert items == ()


def test_reported_business_segments_preserve_materiality() -> None:
    as_of = datetime(2026, 7, 27, 15, 57, tzinfo=BEIJING)
    items = collect_cninfo_company_profiles(
        (_company(),),
        as_of_time=as_of,
        observed_at=datetime(2026, 7, 27, 16, 5, tzinfo=BEIJING),
        fetch_profile=lambda _: pd.DataFrame(),
        fetch_business_segments=lambda _: pd.DataFrame(
            [
                {
                    "报告日期": date(2025, 12, 31),
                    "分类类型": "按产品分类",
                    "主营构成": "锂离子电池材料",
                    "收入比例": 0.903942,
                    "利润比例": 0.864438,
                    "毛利率": 0.212693,
                },
                {
                    "报告日期": date(2025, 12, 31),
                    "分类类型": "按产品分类",
                    "主营构成": "日化材料及特种化学品",
                    "收入比例": 0.077152,
                    "利润比例": 0.103726,
                    "毛利率": 0.299021,
                },
            ]
        ),
    )

    assert len(items) == 1
    assert items[0].payload["evidence_kind"] == "reported_business_segments"
    assert items[0].payload["revenue_share"] == 0.903942
    assert items[0].payload["gross_profit_share"] == 0.864438
