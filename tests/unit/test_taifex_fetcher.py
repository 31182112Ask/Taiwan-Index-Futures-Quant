from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from tifq.data.taifex_fetcher import (
    TAIFEX_RECENT_FUTURES_URL,
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


def onclick_row(trading_date: str) -> str:
    return (
        "<tr>"
        f"<td align='center'>{trading_date} PM 04:43:39</td>"
        f"<td align='center'>{trading_date}</td>"
        "<td align='center'><input type='button' value='下載' "
        "onClick=\"javascript:window.open('https://www.taifex.com.tw/file/taifex/"
        "Dailydownload/Dailydownload/Daily_2026_06_18.zip')\"></td>"
        "<td align='center'><input type='button' value='下載' "
        "onClick=\"javascript:window.open('https://www.taifex.com.tw/file/taifex/"
        "Dailydownload/DailydownloadCSV/Daily_2026_06_18.zip')\"></td>"
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


def multi_sync_client(page_html: str, bodies: dict[str, bytes]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == TAIFEX_RECENT_FUTURES_URL:
            return httpx.Response(200, text=page_html, request=request)
        for filename, body in bodies.items():
            if url.endswith(filename):
                return httpx.Response(
                    200,
                    content=body,
                    headers={"content-type": "text/csv"},
                    request=request,
                )
        return httpx.Response(404, request=request)

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


def test_discovers_official_onclick_csv_zip_and_ignores_rpt_zip() -> None:
    html = official_page(onclick_row("2026/06/18"))

    files = discover_recent_taifex_csv_files(limit=30, client=mock_client(html))

    assert len(files) == 1
    assert files[0].trading_date.isoformat() == "2026-06-18"
    assert files[0].download_url == (
        "https://www.taifex.com.tw/file/taifex/Dailydownload/"
        "DailydownloadCSV/Daily_2026_06_18.zip"
    )
    assert files[0].remote_filename == "Daily_2026_06_18.zip"


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


def test_discovery_excludes_future_trading_dates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tifq.data.taifex_fetcher._taipei_today",
        lambda: date(2026, 6, 19),
    )
    html = official_page(
        row("2026/06/22", "/file/Daily_20260622.csv"),
        row("2026/06/18", "/file/Daily_20260618.csv"),
    )

    files = discover_recent_taifex_csv_files(limit=1, client=mock_client(html))

    assert [file.trading_date for file in files] == [date(2026, 6, 18)]


def test_discovery_retries_http_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tifq.data.taifex_fetcher.time.sleep", lambda _: None)
    attempts = 0
    html = official_page(row("2026/06/18", "/file/Daily_20260618.csv"))

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(500, request=request)
        return httpx.Response(200, text=html, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)

    files = discover_recent_taifex_csv_files(limit=1, client=client)

    assert attempts == 2
    assert files[0].trading_date.isoformat() == "2026-06-18"


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
    assert summary.files_failed == 0
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


def test_download_failure_returns_failure_summary_and_cleans_part_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tifq.data.taifex_fetcher.time.sleep", lambda _: None)
    html = official_page(row("2026/06/18", "/file/Daily_20260618.csv"))

    summary = sync_recent_taifex_csv_files(tmp_path, limit=1, client=sync_client(html, b""))

    assert summary.files_failed == 1
    assert "empty response" in summary.failures[0].error
    assert list(tmp_path.rglob("*.part")) == []
    assert not list(tmp_path.rglob("*.csv"))


def test_mid_sync_failure_preserves_successful_manifest_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tifq.data.taifex_fetcher.time.sleep", lambda _: None)
    html = official_page(
        row("2026/06/18", "/file/Daily_20260618.csv"),
        row("2026/06/17", "/file/Daily_20260617.csv"),
    )

    summary = sync_recent_taifex_csv_files(
        tmp_path,
        limit=2,
        client=multi_sync_client(
            html,
            {
                "Daily_20260618.csv": b"symbol,price\nTMF,100\n",
                "Daily_20260617.csv": b"<html>Error</html>",
            },
        ),
    )

    manifest = json.loads((tmp_path / "download_manifest.json").read_text(encoding="utf-8"))
    assert summary.files_downloaded == 1
    assert summary.files_failed == 1
    assert len(manifest) == 1
    assert manifest[0]["remote_filename"] == "Daily_20260618.csv"


def test_unmanaged_existing_file_is_not_overwritten_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tifq.data.taifex_fetcher.time.sleep", lambda _: None)
    html = official_page(row("2026/06/18", "/file/Daily_20260618.csv"))
    existing = tmp_path / "official" / "2026-06-18" / "Daily_20260618.csv"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"manual\n")

    summary = sync_recent_taifex_csv_files(
        tmp_path,
        limit=1,
        client=sync_client(html, b"remote\n"),
    )

    assert summary.files_failed == 1
    assert "without a matching manifest" in summary.failures[0].error
    assert existing.read_bytes() == b"manual\n"
