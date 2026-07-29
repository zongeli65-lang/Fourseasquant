import io
import json
from pathlib import Path

import pandas as pd

from pypdf import PdfWriter

from fourseasquant.cninfo_announcement import (
    OfficialBuybackDocument,
    enrich_cancelled_buyback_announcements,
    extract_buyback_amount_and_shares,
    read_official_buyback_document,
)


def test_extract_buyback_amount_and_shares_scales_chinese_units() -> None:
    text = """
    本次回购已实施完成并办理注销。
    累计已回购股数 1,080.30 万股
    累计已回购金额 19,863.82 万元
    """

    amount, shares = extract_buyback_amount_and_shares(text)

    assert amount == 198_638_200
    assert shares == 10_803_000


def test_extract_cancelled_buyback_uses_actual_transaction_total() -> None:
    text = """
    累计回购股份数量为 4,150,000 股，
    成交总金额为 199,989,793.57 元人民币（不含交易费用）。
    公司本次注销的回购股份数量为 4,150,000 股。
    """

    amount, shares = extract_buyback_amount_and_shares(text)

    assert amount == 199_989_793.57
    assert shares == 4_150_000


def test_read_official_document_resolves_and_archives_cninfo_pdf(
    tmp_path: Path,
) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    pdf = io.BytesIO()
    writer.write(pdf)
    requests: list[tuple[str, bytes | None]] = []

    def fetch(url: str, body: bytes | None) -> bytes:
        requests.append((url, body))
        if "bulletin_detail" in url:
            return json.dumps(
                {
                    "announcement": {
                        "adjunctUrl": "finalpage/2026-07-24/123.PDF"
                    }
                }
            ).encode()
        return pdf.getvalue()

    document = read_official_buyback_document(
        (
            "http://www.cninfo.com.cn/new/disclosure/detail"
            "?stockCode=600000&announcementId=123"
            "&orgId=x&announcementTime=2026-07-24"
        ),
        fetch_bytes=fetch,
        storage_directory=tmp_path,
    )

    assert document.pdf_url == (
        "http://static.cninfo.com.cn/finalpage/2026-07-24/123.PDF"
    )
    assert document.amount_cny is None
    assert document.shares is None
    assert len(document.content_sha256) == 64
    assert document.local_path is not None
    assert Path(document.local_path).read_bytes() == pdf.getvalue()
    assert requests[0][1] == b""
    assert requests[1][1] is None


def test_pdf_failure_is_preserved_as_unverified_metadata() -> None:
    frame = pd.DataFrame(
        [
            {
                "代码": "600000",
                "简称": "浦发银行",
                "公告标题": "关于完成<em>回购</em>股份注销的公告",
                "公告时间": "2026-07-24",
                "公告链接": "https://example.test/detail",
            }
        ]
    )

    enriched = enrich_cancelled_buyback_announcements(
        frame,
        document_reader=lambda _url: (_ for _ in ()).throw(
            ValueError("损坏的 PDF")
        ),
    )

    assert enriched.loc[0, "注销金额"] is None
    assert enriched.loc[0, "注销股份数量"] is None
    assert enriched.loc[0, "官方解析错误"] == "ValueError: 损坏的 PDF"


def test_enrichment_records_official_hash_and_local_path() -> None:
    frame = pd.DataFrame(
        [
            {
                "代码": "600000",
                "简称": "浦发银行",
                "公告标题": "回购股份注销完成公告",
                "公告时间": "2026-07-24",
                "公告链接": "https://example.test/detail",
            }
        ]
    )
    document = OfficialBuybackDocument(
        pdf_url="https://example.test/report.pdf",
        amount_cny=100,
        shares=10,
        content_sha256="a" * 64,
        local_path="/tmp/a.pdf",
    )

    enriched = enrich_cancelled_buyback_announcements(
        frame,
        document_reader=lambda _url: document,
    )

    assert enriched.loc[0, "官方文件哈希"] == "a" * 64
    assert enriched.loc[0, "官方文件本机路径"] == "/tmp/a.pdf"


def test_corrupt_pdf_keeps_archive_mapping_and_parse_error(
    tmp_path: Path,
) -> None:
    def fetch(url: str, _body: bytes | None) -> bytes:
        if "bulletin_detail" in url:
            return json.dumps(
                {
                    "announcement": {
                        "adjunctUrl": "finalpage/2026-07-24/broken.PDF"
                    }
                }
            ).encode()
        return b"not-a-pdf"

    document = read_official_buyback_document(
        (
            "http://www.cninfo.com.cn/new/disclosure/detail"
            "?announcementId=broken&announcementTime=2026-07-24"
        ),
        fetch_bytes=fetch,
        storage_directory=tmp_path,
    )

    assert document.parse_error is not None
    assert document.local_path is not None
    assert Path(document.local_path).read_bytes() == b"not-a-pdf"
    assert len(document.content_sha256) == 64
