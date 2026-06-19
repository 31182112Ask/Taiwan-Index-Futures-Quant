from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from tifq.data.taifex_fetcher import (
    TAIFEX_RECENT_FUTURES_URL,
    TaifexFetchError,
    discover_recent_taifex_csv_files,
    sync_recent_taifex_csv_files,
)


def official_page(*rows: str) -> str:
    return f"<html><body><table>{''.join(rows)}</table></body></html>"


def row(
    trading_date: str,
    csv_href: str,
    *,
    rpt_href: str = "/downloads/file.rpt",
    label: str = "CSV",
) -> str:
    return (
        "<tr>"
        f"<td>{trading_date} PM 04:40:00</td>"
        f"<td>{trading_date}</td>"
        f"<td><a href='{rpt_href}'>RPT</a></td>"
        f"<td><a href='{csv_href}'>{label}</a></td>"
        "</tr>"
    )


def mock_client(html: str) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def sync_client(page_html: str, body: bytes = b"symbol,price\nTMF,100\n") -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TAIFEX_RECENT_FUTURES_URL:
            return httpx.Response(200, text=page_html, request=request)
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "text/csv"},
            request=request,
        )

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_discovers_csv_links_only_and_resolves_relative_urls() -> None:
    html = official_page(
        row("2026/06/18", "/file/Daily_20260618.rpt", label="RPT"),
        row("2026/06/17", "/file/Daily_20260617.csv"),
    )
    client = mock_client(html)

    files = discover_recent_taifex_csv_files(limit=30, client=client)

    assert len(files) == 1
    assert files[0].trading_date.isoformat() == "2026-06-17"
    assert files[0].download_url == "https://www.taifex.com.tw/file/Daily_20260617.csv"
    assert files[0].remote_filename == "Daily_20260617.csv"


def test_rejects_external_domain_links_and_deduplicates_dates() -> None:
    html = official_page(
        row("2026/06/18", "https://evil.example/Daily_20260618.csv"),
        row("2026/06/17", "/file/Daily_20260617.csv"),
        row("2026/06/17", "/file/Duplicate_20260617.csv"),
    )

    files = discover_recent_taifex_csv_files(limit=30, client=mock_client(html))

    assert [file.trading_date.isoformat() for file in files] == ["2026-06-17"]
    assert files[0].remote_filename == "Daily_20260617.csv"


def test_preserves_official_newest_to_oldest_order_and_applies_limit() -> None:
    html = official_page(
        row("2026/06/18", "/file/Daily_20260618.csv"),
        row("2026/06/17", "/file/Daily_20260617.csv"),
        row("2026/06/16", "/file/Daily_20260616.csv"),
    )

    files = discover_recent_taifex_csv_files(limit=2, client=mock_client(html))

    assert [file.trading_date.isoformat() for file in files] == [
        "2026-06-18",
        "2026-06-17",
    ]


@pytest.mark.parametrize("limit", [0, 31])
def test_enforces_limit_range(limit: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 30"):
        discover_recent_taifex_csv_files(limit=limit, client=mock_client(""))


def test_sync_downloads_file_and_writes_sha_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tifq.data.taifex_fetcher.time.sleep", lambda _: None)
    body = b"symbol,price\nTMF,100\n"
    html = official_page(row("2026/06/18", "/file/Daily_20260618.csv"))

    summary = sync_recent_taifex_csv_files(tmp_path, limit=1, client=sync_client(html, body))

    record = summary.records[0]
    assert summary.files_downloaded == 1
    assert record.local_path.exists()
    assert record.size_bytes == len(body)
    assert record.sha256 == hashlib.sha256(body).hexdigest()
    manifest = json.loads((tmp_path / "download_manifest.json").read_text(encoding="utf-8"))
    assert manifest[0]["sha256"] == record.sha256


def test_repeated_sync_skips_existing_valid_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tifq.data.taifex_fetcher.time.sleep", lambda _: None)
    html = official_page(row("2026/06/18", "/file/Daily_20260618.csv"))
    sync_recent_taifex_csv_files(tmp_path, limit=1, client=sync_client(html))

    summary = sync_recent_taifex_csv_files(tmp_path, limit=1, client=sync_client(html))

    assert summary.files_skipped == 1
    assert summary.records[0].status == "skipped"


def test_overwrite_updates_existing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tifq.data.taifex_fetcher.time.sleep", lambda _: None)
    html = official_page(row("2026/06/18", "/file/Daily_20260618.csv"))
    sync_recent_taifex_csv_files(tmp_path, limit=1, client=sync_client(html, b"old\n"))

    summary = sync_recent_taifex_csv_files(
        tmp_path,
        limit=1,
        overwrite=True,
        client=sync_client(html, b"new\n"),
    )

    assert summary.files_updated == 1
    assert summary.records[0].local_path.read_bytes() == b"new\n"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"", "empty response"),
        (b"<html><body>Error</body></html>", "HTML error"),
    ],
)
def test_rejects_bad_download_bodies_and_cleans_part_files(
    tmp_path: Path,
    body: bytes,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tifq.data.taifex_fetcher.time.sleep", lambda _: None)
    html = official_page(row("2026/06/18", "/file/Daily_20260618.csv"))

    with pytest.raises(TaifexFetchError, match=message):
        sync_recent_taifex_csv_files(tmp_path, limit=1, client=sync_client(html, body))

    assert list(tmp_path.rglob("*.part")) == []
    assert not list(tmp_path.rglob("*.csv"))
