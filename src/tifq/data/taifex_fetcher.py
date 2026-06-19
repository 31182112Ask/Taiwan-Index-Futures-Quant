"""Official TAIFEX recent futures CSV discovery and download helpers."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup  # type: ignore[import-untyped]

TAIFEX_RECENT_FUTURES_URL = "https://www.taifex.com.tw/cht/3/dlFutPrevious30DaysSalesData"
TAIFEX_HOST = "www.taifex.com.tw"
USER_AGENT = "Taiwan-Index-Futures-Quant/0.1 local research data sync"
MANIFEST_FILENAME = "download_manifest.json"
MAX_RECENT_FILES = 30
MAX_DOWNLOAD_ATTEMPTS = 3

_DATE_PATTERNS = (
    re.compile(r"(?P<year>\d{4})[/-](?P<month>\d{1,2})[/-](?P<day>\d{1,2})"),
    re.compile(r"(?<!\d)(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})(?!\d)"),
)
_SCRIPT_URL_RE = re.compile(r"""['"](?P<url>https?://[^'"]+|/[^'"]+)['"]""")
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class TaifexFetchError(RuntimeError):
    """Raised when official TAIFEX discovery or download fails."""


@dataclass(frozen=True)
class TaifexRemoteFile:
    """One official remote CSV file advertised by TAIFEX."""

    trading_date: date
    download_url: str
    remote_filename: str


@dataclass(frozen=True)
class TaifexDownloadRecord:
    """One local official download record."""

    trading_date: date
    source_url: str
    local_path: Path
    size_bytes: int
    sha256: str
    status: str


@dataclass(frozen=True)
class TaifexDownloadFailure:
    """One failed official download attempt."""

    trading_date: date
    download_url: str
    remote_filename: str
    local_path: Path
    error: str


@dataclass(frozen=True)
class TaifexFetchSummary:
    """Summary of one recent TAIFEX sync operation."""

    files_discovered: int
    files_selected: int
    files_downloaded: int
    files_skipped: int
    files_updated: int
    files_failed: int
    records: tuple[TaifexDownloadRecord, ...]
    failures: tuple[TaifexDownloadFailure, ...]


def discover_recent_taifex_csv_files(
    *,
    limit: int = MAX_RECENT_FILES,
    client: httpx.Client | None = None,
) -> list[TaifexRemoteFile]:
    """Discover recent official TAIFEX futures time-and-sales CSV download links."""
    _validate_limit(limit)
    owns_client = client is None
    active_client = client or _make_client(60.0)
    try:
        response = _request_with_retries(
            active_client,
            TAIFEX_RECENT_FUTURES_URL,
            "TAIFEX discovery",
        )
        discovered = parse_recent_taifex_csv_files(response.text, TAIFEX_RECENT_FUTURES_URL)
        return discovered[:limit]
    finally:
        if owns_client:
            active_client.close()


def parse_recent_taifex_csv_files(html: str, source_url: str) -> list[TaifexRemoteFile]:
    """Parse official recent CSV links from TAIFEX page HTML."""
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[TaifexRemoteFile] = []

    rows = soup.find_all("tr")
    if rows:
        for row in rows:
            candidates.extend(_remote_files_from_container(row, source_url))
    else:
        candidates.extend(_remote_files_from_container(soup, source_url))

    unique: dict[date, TaifexRemoteFile] = {}
    for candidate in candidates:
        unique.setdefault(candidate.trading_date, candidate)
    return list(unique.values())


def sync_recent_taifex_csv_files(
    raw_dir: str | Path,
    *,
    limit: int = MAX_RECENT_FILES,
    overwrite: bool = False,
    timeout_seconds: float = 60.0,
    client: httpx.Client | None = None,
) -> TaifexFetchSummary:
    """Download recent official TAIFEX CSV files into the V1 raw data directory."""
    _validate_limit(limit)
    raw_path = Path(raw_dir)
    raw_path.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(raw_path)
    records_by_key = _manifest_records_by_key(manifest)

    owns_client = client is None
    active_client = client or _make_client(timeout_seconds)
    try:
        discovered_files = discover_recent_taifex_csv_files(
            limit=MAX_RECENT_FILES,
            client=active_client,
        )
        remote_files = discovered_files[:limit]
        records: list[TaifexDownloadRecord] = []
        failures: list[TaifexDownloadFailure] = []
        for index, remote_file in enumerate(remote_files):
            try:
                record = _sync_one_file(
                    raw_path,
                    remote_file,
                    client=active_client,
                    manifest_record=records_by_key.get(_manifest_key(remote_file)),
                    overwrite=overwrite,
                )
            except (OSError, TaifexFetchError) as exc:
                failures.append(
                    TaifexDownloadFailure(
                        trading_date=remote_file.trading_date,
                        download_url=remote_file.download_url,
                        remote_filename=remote_file.remote_filename,
                        local_path=_local_download_path(raw_path, remote_file),
                        error=str(exc),
                    )
                )
                continue

            records.append(record)
            if record.status in {"downloaded", "updated"}:
                _upsert_manifest_record(manifest, remote_file, record)
                _write_manifest(raw_path, manifest)
                if index < len(remote_files) - 1:
                    time.sleep(0.1)
    finally:
        if owns_client:
            active_client.close()

    _write_manifest(raw_path, manifest)
    return TaifexFetchSummary(
        files_discovered=len(discovered_files),
        files_selected=len(remote_files),
        files_downloaded=sum(1 for record in records if record.status == "downloaded"),
        files_skipped=sum(1 for record in records if record.status == "skipped"),
        files_updated=sum(1 for record in records if record.status == "updated"),
        files_failed=len(failures),
        records=tuple(records),
        failures=tuple(failures),
    )


def _remote_files_from_container(container: Any, source_url: str) -> list[TaifexRemoteFile]:
    text = container.get_text(" ", strip=True)
    row_date = _extract_trading_date(text)
    files: list[TaifexRemoteFile] = []
    for element in container.find_all(["a", "button", "input"]):
        label = _element_label(element)
        for href in _element_download_targets(element):
            if not _looks_like_csv_link(href, label):
                continue
            download_url = _validated_taifex_url(href, source_url)
            if download_url is None:
                continue
            filename = _remote_filename(download_url)
            trading_date = row_date or _extract_trading_date(f"{filename} {download_url}")
            if trading_date is None:
                continue
            files.append(TaifexRemoteFile(trading_date, download_url, filename))
    return files


def _element_label(element: Any) -> str:
    text = element.get_text(" ", strip=True)
    value = str(element.get("value") or "")
    title = str(element.get("title") or "")
    return " ".join(part for part in (text, value, title) if part)


def _element_download_targets(element: Any) -> list[str]:
    targets: list[str] = []
    href = str(element.get("href") or "").strip()
    if href:
        targets.append(href)

    onclick = str(element.get("onclick") or element.get("onClick") or "").strip()
    targets.extend(match.group("url").strip() for match in _SCRIPT_URL_RE.finditer(onclick))
    return targets


def _looks_like_csv_link(href: str, label: str) -> bool:
    combined = f"{href} {label}".lower()
    if not href or href.lower().startswith(("javascript:", "mailto:", "#")):
        return False
    if ".rpt" in combined or "rpt" == label.strip().lower():
        return False
    if "dailydownloadcsv" in combined:
        return href.lower().endswith((".csv", ".zip"))
    return ".csv" in combined or "csv" in label.strip().lower()


def _validated_taifex_url(href: str, source_url: str) -> str | None:
    resolved = urljoin(source_url, href)
    parsed = urlparse(resolved)
    if parsed.scheme != "https":
        return None
    if parsed.hostname != TAIFEX_HOST:
        return None
    if not parsed.path:
        return None
    return resolved


def _extract_trading_date(text: str) -> date | None:
    matches: list[date] = []
    normalized = text.replace("\u200e", "").replace("\u200f", "")
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(normalized):
            try:
                matches.append(
                    date(
                        int(match.group("year")),
                        int(match.group("month")),
                        int(match.group("day")),
                    )
                )
            except ValueError:
                continue
    if not matches:
        return None
    return matches[-1]


def _sync_one_file(
    raw_dir: Path,
    remote_file: TaifexRemoteFile,
    *,
    client: httpx.Client,
    manifest_record: dict[str, Any] | None,
    overwrite: bool,
) -> TaifexDownloadRecord:
    local_path = _local_download_path(raw_dir, remote_file)
    local_path.parent.mkdir(parents=True, exist_ok=True)

    if not overwrite and _manifest_record_matches_file(manifest_record, local_path):
        if manifest_record is None:
            raise TaifexFetchError("manifest record unexpectedly missing after validation")
        return TaifexDownloadRecord(
            trading_date=remote_file.trading_date,
            source_url=TAIFEX_RECENT_FUTURES_URL,
            local_path=local_path,
            size_bytes=int(manifest_record["size_bytes"]),
            sha256=str(manifest_record["sha256"]),
            status="skipped",
        )
    if local_path.exists() and not overwrite:
        raise TaifexFetchError(
            f"Local TAIFEX file exists without a matching manifest record: {local_path}"
        )

    existing = local_path.exists()
    body, content_type = _download_with_retries(client, remote_file.download_url)
    _validate_download_body(body, content_type, remote_file.remote_filename)

    part_path = local_path.with_name(local_path.name + ".part")
    try:
        part_path.write_bytes(body)
        sha256 = _sha256_bytes(body)
        size_bytes = len(body)
        part_path.replace(local_path)
    except Exception:
        part_path.unlink(missing_ok=True)
        raise

    return TaifexDownloadRecord(
        trading_date=remote_file.trading_date,
        source_url=TAIFEX_RECENT_FUTURES_URL,
        local_path=local_path,
        size_bytes=size_bytes,
        sha256=sha256,
        status="updated" if existing else "downloaded",
    )


def _download_with_retries(client: httpx.Client, url: str) -> tuple[bytes, str]:
    response = _request_with_retries(client, url, "TAIFEX download")
    return response.content, response.headers.get("content-type", "")


def _request_with_retries(client: httpx.Client, url: str, label: str) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(MAX_DOWNLOAD_ATTEMPTS):
        try:
            response = client.get(url, headers=_request_headers())
            if response.status_code == 429 or response.status_code >= 500:
                raise TaifexFetchError(
                    f"{label} returned retryable status {response.status_code}: {url}"
                )
            response.raise_for_status()
            return response
        except (httpx.TransportError, httpx.HTTPStatusError, TaifexFetchError) as exc:
            last_error = exc
            if attempt == MAX_DOWNLOAD_ATTEMPTS - 1:
                break
            time.sleep(0.2 * (2**attempt))
    raise TaifexFetchError(f"{label} failed after retries: {url}") from last_error


def _validate_download_body(body: bytes, content_type: str, filename: str) -> None:
    if not body:
        raise TaifexFetchError("TAIFEX download returned an empty response body")
    head = body[:512].lstrip().lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
        raise TaifexFetchError("TAIFEX download returned an HTML error page")

    suffix = Path(filename).suffix.lower()
    normalized_content_type = content_type.lower()
    allowed_by_name = suffix in {".csv", ".zip"}
    allowed_by_type = any(
        value in normalized_content_type
        for value in (
            "csv",
            "text/plain",
            "application/octet-stream",
            "application/zip",
            "zip",
        )
    )
    if not allowed_by_name and not allowed_by_type:
        raise TaifexFetchError(f"Unsupported TAIFEX download content type: {content_type}")


def _load_manifest(raw_dir: Path) -> list[dict[str, Any]]:
    manifest_path = raw_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        return []
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        message = f"Could not parse TAIFEX download manifest: {manifest_path}"
        raise TaifexFetchError(message) from exc
    if not isinstance(payload, list):
        raise TaifexFetchError("TAIFEX download manifest must contain a JSON list")
    records: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise TaifexFetchError("TAIFEX download manifest contains a non-object record")
        records.append(item)
    return records


def _write_manifest(raw_dir: Path, records: list[dict[str, Any]]) -> None:
    manifest_path = raw_dir / MANIFEST_FILENAME
    part_path = manifest_path.with_name(manifest_path.name + ".part")
    part_path.write_text(
        json.dumps(records, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    part_path.replace(manifest_path)


def _manifest_records_by_key(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        trading_date = str(record.get("trading_date", ""))
        download_url = str(record.get("download_url", ""))
        if trading_date and download_url:
            result[f"{trading_date}|{download_url}"] = record
    return result


def _manifest_key(remote_file: TaifexRemoteFile) -> str:
    return f"{remote_file.trading_date.isoformat()}|{remote_file.download_url}"


def _manifest_record_matches_file(record: dict[str, Any] | None, local_path: Path) -> bool:
    if record is None or not local_path.exists():
        return False
    expected_sha = record.get("sha256")
    expected_size = record.get("size_bytes")
    if not isinstance(expected_sha, str) or not isinstance(expected_size, int):
        return False
    data = local_path.read_bytes()
    return len(data) == expected_size and _sha256_bytes(data) == expected_sha


def _upsert_manifest_record(
    records: list[dict[str, Any]],
    remote_file: TaifexRemoteFile,
    download_record: TaifexDownloadRecord,
) -> None:
    payload = {
        **asdict(download_record),
        "trading_date": download_record.trading_date.isoformat(),
        "source_page": TAIFEX_RECENT_FUTURES_URL,
        "download_url": remote_file.download_url,
        "remote_filename": remote_file.remote_filename,
        "local_path": str(download_record.local_path),
        "downloaded_at": datetime.now(tz=UTC).isoformat(),
    }
    key = _manifest_key(remote_file)
    for index, existing in enumerate(records):
        existing_key = f"{existing.get('trading_date', '')}|{existing.get('download_url', '')}"
        if existing_key == key:
            records[index] = payload
            return
    records.append(payload)


def _local_download_path(raw_dir: Path, remote_file: TaifexRemoteFile) -> Path:
    filename = _sanitize_filename(remote_file.remote_filename)
    return raw_dir / "official" / remote_file.trading_date.isoformat() / filename


def _remote_filename(download_url: str) -> str:
    parsed = urlparse(download_url)
    filename = Path(unquote(parsed.path)).name
    if not filename:
        filename = f"taifex_{_extract_trading_date(download_url) or 'download'}.csv"
    return _sanitize_filename(filename)


def _sanitize_filename(filename: str) -> str:
    cleaned = _SAFE_FILENAME_RE.sub("_", Path(filename).name).strip("._")
    if not cleaned:
        raise TaifexFetchError("Remote TAIFEX filename is empty after sanitization")
    return cleaned


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_client(timeout_seconds: float) -> httpx.Client:
    return httpx.Client(
        follow_redirects=True,
        timeout=timeout_seconds,
        headers=_request_headers(),
    )


def _request_headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT}


def _validate_limit(limit: int) -> None:
    if not 1 <= limit <= MAX_RECENT_FILES:
        raise ValueError("limit must be between 1 and 30")
