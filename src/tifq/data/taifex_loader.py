"""TAIFEX historical CSV/ZIP importer for V1 tick data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

from tifq.data.schemas import TICK_REQUIRED_COLUMNS, V1_SYMBOL
from tifq.data.storage import tick_path, write_parquet
from tifq.data.tick_cleaner import clean_tick_frame


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
        "\u5230\u671f\u6708\u4efd",
        "\u5230\u671f\u6708\u4efd(\u9031\u5225)",
        "\u5951\u7d04\u6708\u4efd",
        "\u5951\u7d04",
    ),
    "timestamp": (
        "timestamp",
        "datetime",
        "date_time",
        "\u4ea4\u6613\u6642\u9593",
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
        "\u6210\u4ea4\u6578\u91cf",
        "\u6210\u4ea4\u91cf",
        "\u6578\u91cf",
    ),
}


def import_taifex_ticks(
    raw_dir: str | Path,
    processed_dir: str | Path,
    *,
    symbol: str = V1_SYMBOL,
) -> ImportSummary:
    """Import local TAIFEX CSV/ZIP files into cleaned daily tick Parquet files."""
    if symbol != V1_SYMBOL:
        raise ValueError(f"V1 supports symbol {V1_SYMBOL} only; got: {symbol}")

    raw_path = Path(raw_dir)
    processed_path = Path(processed_dir)
    raw_files = discover_taifex_files(raw_path)
    if not raw_files:
        return ImportSummary(0, 0, 0, 0, 0, ())

    normalized_frames: list[pd.DataFrame] = []
    csv_files_read = 0
    for path in raw_files:
        raw_csvs = _read_raw_file(path)
        csv_files_read += len(raw_csvs)
        normalized_frames.extend(
            _normalize_raw_frame(frame, source) for source, frame in raw_csvs
        )

    if not normalized_frames:
        return ImportSummary(len(raw_files), 0, 0, 0, 0, ())

    normalized = pd.concat(normalized_frames, ignore_index=True)
    clean_result = clean_tick_frame(normalized, symbol=symbol)
    output_paths = _write_daily_ticks(clean_result.ticks, processed_path, symbol)

    return ImportSummary(
        files_discovered=len(raw_files),
        csv_files_read=csv_files_read,
        input_row_count=clean_result.input_row_count,
        output_tick_count=len(clean_result.ticks),
        invalid_row_count=clean_result.invalid_row_count,
        output_paths=tuple(output_paths),
    )


def discover_taifex_files(raw_dir: str | Path) -> list[Path]:
    """Return sorted local TAIFEX CSV and ZIP files under a raw directory."""
    raw_path = Path(raw_dir)
    if not raw_path.exists():
        raise FileNotFoundError(f"Raw directory does not exist: {raw_path}")
    if not raw_path.is_dir():
        raise NotADirectoryError(f"Raw path is not a directory: {raw_path}")

    return sorted(
        path
        for path in raw_path.iterdir()
        if path.is_file() and path.suffix.lower() in {".csv", ".zip"}
    )


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
    if timestamp_column is not None:
        return _column_series(df, timestamp_column)
    if trade_date_column is None or trade_time_column is None:
        raise TaifexImportError("timestamp cannot be built without date and time columns")
    trade_date = _column_series(df, trade_date_column).astype(str).str.strip()
    trade_time = _column_series(df, trade_time_column).astype(str).str.strip()
    return trade_date + " " + trade_time


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


def _ensure_date(value: object) -> date:
    if isinstance(value, date):
        return value
    raise TypeError(f"expected date from timestamp grouping, got {type(value).__name__}")
