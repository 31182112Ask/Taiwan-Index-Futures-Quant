"""TAIFEX historical CSV/ZIP importer for V1 tick data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

from tifq.data.schemas import TICK_REQUIRED_COLUMNS, V1_SYMBOL
from tifq.data.storage import tick_path, write_parquet
from tifq.data.tick_cleaner import clean_tick_frame
from tifq.runtime.locking import OperationLock
from tifq.runtime.manifests import (
    IMPORT_MANIFEST_FILENAME,
    atomic_write_json,
    fingerprint_file,
    load_json_manifest,
    sha256_file,
)
from tifq.runtime.progress import ProgressCallback, ProgressReporter


class TaifexImportError(ValueError):
    """Raised when raw TAIFEX files cannot be normalized."""


@dataclass(frozen=True)
class ImportSummary:
    """Summary of a TAIFEX import run."""

    files_discovered: int
    csv_files_read: int
    input_row_count: int
    output_tick_count: int
    invalid_row_count: int
    output_paths: tuple[Path, ...]
    files_skipped: int = 0
    files_changed: int = 0
    manifest_path: Path | None = None
    no_op: bool = False


PARSER_VERSION = "taifex-v2"


_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "symbol": (
        "symbol",
        "product",
        "product_id",
        "productid",
        "\u5546\u54c1\u4ee3\u865f",
        "\u5546\u54c1",
        "\u5546\u54c1\u5225",
        "\u5546\u54c1\u540d\u7a31",
    ),
    "contract": (
        "contract",
        "contract_month",
        "contractmonth",
        "delivery_month",
        "deliverymonth",
        "到期月份",
        "到期月份(週別)",
        "到期月份（週別）",
        "契約月份",
        "契約",
    ),
    "timestamp": (
        "timestamp",
        "datetime",
        "date_time",
        "\u6210\u4ea4\u6642\u9593\u6233",
        "\u6210\u4ea4\u65e5\u671f\u6642\u9593",
    ),
    "trade_date": (
        "date",
        "trade_date",
        "tradedate",
        "\u4ea4\u6613\u65e5\u671f",
        "\u6210\u4ea4\u65e5\u671f",
        "\u65e5\u671f",
    ),
    "trade_time": (
        "time",
        "trade_time",
        "tradetime",
        "\u4ea4\u6613\u6642\u9593",
        "\u6210\u4ea4\u6642\u9593",
        "\u6642\u9593",
    ),
    "price": (
        "price",
        "trade_price",
        "tradeprice",
        "\u6210\u4ea4\u50f9\u683c",
        "\u6210\u4ea4\u50f9",
        "\u50f9\u683c",
    ),
    "volume": (
        "volume",
        "qty",
        "quantity",
        "trade_volume",
        "tradevolume",
        "成交數量",
        "成交數量(B+S)",
        "成交數量（B+S）",
        "成交量",
        "數量",
    ),
}


def import_taifex_ticks(
    raw_dir: str | Path,
    processed_dir: str | Path,
    *,
    symbol: str = V1_SYMBOL,
    force: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> ImportSummary:
    """Incrementally import local TAIFEX files into cleaned daily tick Parquet."""
    if symbol != V1_SYMBOL:
        raise ValueError(f"V1 supports symbol {V1_SYMBOL} only; got: {symbol}")

    raw_path = Path(raw_dir)
    processed_path = Path(processed_dir)
    raw_files = discover_taifex_files(raw_path)
    manifest_path = processed_path / IMPORT_MANIFEST_FILENAME
    if not raw_files:
        return ImportSummary(0, 0, 0, 0, 0, (), manifest_path=manifest_path, no_op=True)

    quarantine = processed_path.parent / "quarantine" / "manifests"
    manifest = load_json_manifest(
        manifest_path,
        default={"version": 1, "records": []},
        quarantine_dir=quarantine,
    )
    records = _import_manifest_records(manifest)
    reporter = ProgressReporter("taifex_import", progress_callback)
    reporter.update("Import", 0, len(raw_files), "Planning incremental raw import")
    output_paths: list[Path] = []
    csv_files_read = 0
    input_rows = 0
    output_rows = 0
    invalid_rows = 0
    files_skipped = 0
    files_changed = 0

    with OperationLock(processed_path.parent / ".runtime", "raw_import"):
        updated_records = dict(records)
        for index, path in enumerate(raw_files, start=1):
            key = str(path.resolve())
            previous = records.get(key)
            fingerprint = fingerprint_file(path, previous)
            if not force and _unchanged_import_record(previous, fingerprint, PARSER_VERSION):
                files_skipped += 1
                reporter.update("Import", index, len(raw_files), f"Unchanged: {path.name}")
                continue

            raw_csvs = _read_raw_file(path)
            csv_files_read += len(raw_csvs)
            normalized_frames = [
                _normalize_raw_frame(frame, source) for source, frame in raw_csvs
            ]
            if not normalized_frames:
                reporter.update("Import", index, len(raw_files), f"No CSV rows: {path.name}")
                continue
            normalized = pd.concat(normalized_frames, ignore_index=True)
            clean_result = clean_tick_frame(normalized, symbol=symbol)
            source_labels = sorted(set(clean_result.ticks["source"].astype(str)))
            previous_labels = _string_list(previous, "source_labels")
            written = _write_daily_ticks_incremental(
                clean_result.ticks,
                processed_path,
                symbol,
                source_labels=tuple(sorted(set(source_labels + previous_labels))),
            )
            output_paths.extend(written)
            input_rows += clean_result.input_row_count
            output_rows += len(clean_result.ticks)
            invalid_rows += clean_result.invalid_row_count
            files_changed += 1
            updated_records[key] = {
                "source_path": key,
                "size": fingerprint.size,
                "mtime_ns": fingerprint.mtime_ns,
                "sha256": fingerprint.sha256,
                "parser_version": PARSER_VERSION,
                "imported_at": datetime.now(tz=UTC).isoformat(),
                "input_rows": clean_result.input_row_count,
                "output_rows": len(clean_result.ticks),
                "invalid_rows": clean_result.invalid_row_count,
                "source_labels": source_labels,
                "output_paths": [str(output.resolve()) for output in written],
                "output_hashes": {
                    str(output.resolve()): sha256_file(output) for output in written
                },
            }
            reporter.update("Import", index, len(raw_files), f"Imported: {path.name}")

        if files_changed:
            atomic_write_json(
                manifest_path,
                {
                    "version": 1,
                    "parser_version": PARSER_VERSION,
                    "records": list(updated_records.values()),
                },
            )
    reporter.update("Complete", len(raw_files), len(raw_files), "TAIFEX import complete")
    unique_outputs = tuple(sorted(set(output_paths)))
    return ImportSummary(
        files_discovered=len(raw_files),
        csv_files_read=csv_files_read,
        input_row_count=input_rows,
        output_tick_count=output_rows,
        invalid_row_count=invalid_rows,
        output_paths=unique_outputs,
        files_skipped=files_skipped,
        files_changed=files_changed,
        manifest_path=manifest_path,
        no_op=files_changed == 0,
    )


def discover_taifex_files(raw_dir: str | Path) -> list[Path]:
    """Return sorted local TAIFEX CSV and ZIP files under a raw directory."""
    raw_path = Path(raw_dir)
    if not raw_path.exists():
        raise FileNotFoundError(f"Raw directory does not exist: {raw_path}")
    if not raw_path.is_dir():
        raise NotADirectoryError(f"Raw path is not a directory: {raw_path}")

    return sorted(
        {path.resolve() for path in raw_path.rglob("*") if _is_supported_raw_file(path)}
    )


def _is_supported_raw_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in {".csv", ".zip"}


def _read_raw_file(path: Path) -> list[tuple[str, pd.DataFrame]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return [(path.name, _read_csv_path(path))]
    if suffix == ".zip":
        return _read_zip_path(path)
    return []


def _read_zip_path(path: Path) -> list[tuple[str, pd.DataFrame]]:
    frames: list[tuple[str, pd.DataFrame]] = []
    with ZipFile(path) as archive:
        csv_names = sorted(
            name
            for name in archive.namelist()
            if not name.endswith("/") and Path(name).suffix.lower() == ".csv"
        )
        for name in csv_names:
            with archive.open(name) as raw_file:
                content = raw_file.read()
            source = f"{path.name}:{name}"
            frames.append((source, _read_csv_bytes(content, source)))
    return frames


def _read_csv_path(path: Path) -> pd.DataFrame:
    errors: list[str] = []
    for encoding in ("utf-8-sig", "utf-8", "cp950", "big5"):
        try:
            return pd.read_csv(path, dtype=str, encoding=encoding)
        except UnicodeDecodeError as exc:
            errors.append(f"{encoding}: {exc}")
    joined_errors = "; ".join(errors)
    raise TaifexImportError(f"Could not decode CSV file {path}: {joined_errors}")


def _read_csv_bytes(content: bytes, source: str) -> pd.DataFrame:
    errors: list[str] = []
    for encoding in ("utf-8-sig", "utf-8", "cp950", "big5"):
        try:
            return pd.read_csv(BytesIO(content), dtype=str, encoding=encoding)
        except UnicodeDecodeError as exc:
            errors.append(f"{encoding}: {exc}")
    joined_errors = "; ".join(errors)
    raise TaifexImportError(f"Could not decode CSV file {source}: {joined_errors}")


def _normalize_raw_frame(df: pd.DataFrame, source: str) -> pd.DataFrame:
    column_lookup = {_normalize_column_name(column): column for column in df.columns}
    mapped_columns = {
        target: _find_source_column(column_lookup, aliases)
        for target, aliases in _COLUMN_ALIASES.items()
    }

    symbol_column = mapped_columns["symbol"]
    contract_column = mapped_columns["contract"]
    price_column = mapped_columns["price"]
    volume_column = mapped_columns["volume"]
    timestamp_column = mapped_columns["timestamp"]
    trade_date_column = mapped_columns["trade_date"]
    trade_time_column = mapped_columns["trade_time"]

    required = {
        "symbol": symbol_column,
        "contract": contract_column,
        "price": price_column,
        "volume": volume_column,
    }
    missing = [target for target, column in required.items() if column is None]
    if timestamp_column is None and (trade_date_column is None or trade_time_column is None):
        missing.append("timestamp or date/time")
    if missing:
        missing_text = ", ".join(missing)
        raise TaifexImportError(f"{source} is missing required TAIFEX columns: {missing_text}")
    if (
        symbol_column is None
        or contract_column is None
        or price_column is None
        or volume_column is None
    ):
        raise TaifexImportError(f"{source} has incomplete TAIFEX column mapping")

    normalized = pd.DataFrame(
        {
            "symbol": _column_series(df, symbol_column),
            "contract": _column_series(df, contract_column),
            "timestamp": _build_timestamp_column(
                df,
                timestamp_column=timestamp_column,
                trade_date_column=trade_date_column,
                trade_time_column=trade_time_column,
            ),
            "price": _column_series(df, price_column),
            "volume": _column_series(df, volume_column),
            "source": source,
        }
    )
    return normalized.loc[:, list(TICK_REQUIRED_COLUMNS)]


def _find_source_column(
    column_lookup: dict[str, str],
    aliases: tuple[str, ...],
) -> str | None:
    for alias in aliases:
        column = column_lookup.get(_normalize_column_name(alias))
        if column is not None:
            return column
    return None


def _normalize_column_name(column: str) -> str:
    return (
        str(column)
        .strip()
        .lower()
        .replace(" ", "")
        .replace("_", "")
        .replace("-", "")
        .replace("/", "")
    )


def _build_timestamp_column(
    df: pd.DataFrame,
    *,
    timestamp_column: str | None,
    trade_date_column: str | None,
    trade_time_column: str | None,
) -> pd.Series:
    if trade_date_column is not None and trade_time_column is not None:
        trade_date = _column_series(df, trade_date_column).astype(str).str.strip()
        trade_time = _column_series(df, trade_time_column).astype(str).str.strip()
        return trade_date + " " + trade_time
    if timestamp_column is not None:
        return _column_series(df, timestamp_column)
    raise TaifexImportError("timestamp cannot be built without timestamp or date/time columns")


def _column_series(df: pd.DataFrame, column: str) -> pd.Series:
    values = df[column]
    if not isinstance(values, pd.Series):
        raise TaifexImportError(f"column name is not unique: {column}")
    return values


def _write_daily_ticks(df: pd.DataFrame, processed_dir: Path, symbol: str) -> list[Path]:
    if df.empty:
        return []

    output_paths: list[Path] = []
    for trading_date, daily_ticks in df.groupby(df["timestamp"].dt.date, sort=True):
        output_path = tick_path(processed_dir, symbol, _ensure_date(trading_date))
        write_parquet(daily_ticks.reset_index(drop=True), output_path)
        output_paths.append(output_path)
    return output_paths


def _write_daily_ticks_incremental(
    df: pd.DataFrame,
    processed_dir: Path,
    symbol: str,
    *,
    source_labels: tuple[str, ...],
) -> list[Path]:
    if df.empty:
        return []
    output_paths: list[Path] = []
    for trading_date, daily_ticks in df.groupby(df["timestamp"].dt.date, sort=True):
        output_path = tick_path(processed_dir, symbol, _ensure_date(trading_date))
        frames = [daily_ticks]
        if output_path.exists():
            existing = pd.read_parquet(output_path)
            existing = existing.loc[~existing["source"].astype(str).isin(source_labels)]
            if not existing.empty:
                frames.insert(0, existing)
        combined = pd.concat(frames, ignore_index=True)
        combined = combined.drop_duplicates().sort_values("timestamp", kind="mergesort")
        write_parquet(combined.reset_index(drop=True), output_path)
        output_paths.append(output_path)
    return output_paths


def _import_manifest_records(manifest: object) -> dict[str, dict[str, object]]:
    if not isinstance(manifest, dict):
        return {}
    raw_records = manifest.get("records")
    if not isinstance(raw_records, list):
        return {}
    records: dict[str, dict[str, object]] = {}
    for record in raw_records:
        if isinstance(record, dict) and isinstance(record.get("source_path"), str):
            records[str(record["source_path"])] = record
    return records


def _unchanged_import_record(
    record: dict[str, object] | None,
    fingerprint: object,
    parser_version: str,
) -> bool:
    if record is None:
        return False
    if record.get("parser_version") != parser_version:
        return False
    if record.get("sha256") != getattr(fingerprint, "sha256", None):
        return False
    paths = _string_list(record, "output_paths")
    return bool(paths) and all(Path(path).exists() for path in paths)


def _string_list(record: dict[str, object] | None, key: str) -> list[str]:
    if record is None:
        return []
    value = record.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _ensure_date(value: object) -> date:
    if isinstance(value, date):
        return value
    raise TypeError(f"expected date from timestamp grouping, got {type(value).__name__}")
