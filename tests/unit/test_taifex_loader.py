from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest

from tifq.data.storage import read_parquet
from tifq.data.taifex_loader import (
    TaifexImportError,
    discover_taifex_files,
    import_taifex_ticks,
)


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_import_taifex_ticks_from_csv_writes_daily_parquet(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    raw_dir.mkdir()
    write_text(
        raw_dir / "ticks.csv",
        "\n".join(
            [
                "symbol,contract,timestamp,price,volume",
                "TMF,202606,2026-06-17 08:45:00,22000,1",
                "TX,202606,2026-06-17 08:45:01,22001,1",
                "TMF,202606,2026-06-17 08:45:02,-1,1",
                "TMF,202606,2026-06-18 08:45:00,22100,2",
            ]
        ),
    )

    summary = import_taifex_ticks(raw_dir, processed_dir)

    assert summary.files_discovered == 1
    assert summary.csv_files_read == 1
    assert summary.input_row_count == 4
    assert summary.output_tick_count == 2
    assert summary.invalid_row_count == 2
    assert summary.output_paths == (
        processed_dir / "ticks" / "TMF" / "2026-06-17.parquet",
        processed_dir / "ticks" / "TMF" / "2026-06-18.parquet",
    )
    day_one = read_parquet(summary.output_paths[0])
    assert day_one["symbol"].tolist() == ["TMF"]
    assert day_one["price"].tolist() == [22000]
    assert str(day_one["timestamp"].dt.tz) == "Asia/Taipei"


def test_import_taifex_ticks_from_zip_uses_column_mapping(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    raw_dir.mkdir()
    csv_content = "\n".join(
        [
            (
                "\u5546\u54c1\u4ee3\u865f,\u5230\u671f\u6708\u4efd,"
                "\u6210\u4ea4\u65e5\u671f,\u6210\u4ea4\u6642\u9593,"
                "\u6210\u4ea4\u50f9\u683c,\u6210\u4ea4\u6578\u91cf"
            ),
            "TMF,202606,2026-06-17,08:45:00,22000,1",
            "TMF,202606,2026-06-17,08:45:01,22001,2",
        ]
    )
    with ZipFile(raw_dir / "taifex.zip", "w") as archive:
        archive.writestr("inner/ticks.csv", csv_content)
        archive.writestr("notes.txt", "ignored")

    summary = import_taifex_ticks(raw_dir, processed_dir)

    assert summary.files_discovered == 1
    assert summary.csv_files_read == 1
    assert summary.output_tick_count == 2
    loaded = read_parquet(summary.output_paths[0])
    assert loaded["contract"].tolist() == ["202606", "202606"]
    assert loaded["source"].tolist() == ["taifex.zip:inner/ticks.csv", "taifex.zip:inner/ticks.csv"]


def test_import_taifex_ticks_prefers_trade_date_and_time_columns(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    raw_dir.mkdir()
    write_text(
        raw_dir / "ticks.csv",
        "\n".join(
            [
                (
                    "\u5546\u54c1\u4ee3\u865f,\u5230\u671f\u6708\u4efd,"
                    "\u4ea4\u6613\u65e5\u671f,\u4ea4\u6613\u6642\u9593,"
                    "\u6210\u4ea4\u50f9\u683c,\u6210\u4ea4\u6578\u91cf"
                ),
                "TMF,202606,2026-06-17,08:45:00,22000,1",
            ]
        ),
    )

    summary = import_taifex_ticks(raw_dir, processed_dir)

    assert summary.output_paths == (processed_dir / "ticks" / "TMF" / "2026-06-17.parquet",)
    loaded = read_parquet(summary.output_paths[0])
    assert loaded.loc[0, "timestamp"].date().isoformat() == "2026-06-17"


def test_discover_taifex_files_returns_only_csv_and_zip(tmp_path: Path) -> None:
    write_text(tmp_path / "a.csv", "")
    write_text(tmp_path / "b.zip", "")
    write_text(tmp_path / "c.txt", "")

    assert discover_taifex_files(tmp_path) == [tmp_path / "a.csv", tmp_path / "b.zip"]


def test_discover_taifex_files_recurses_into_official_download_layout(tmp_path: Path) -> None:
    nested_dir = tmp_path / "official" / "2026-06-18"
    nested_dir.mkdir(parents=True)
    top_level = tmp_path / "manual.csv"
    nested = nested_dir / "Daily_20260618.csv"
    part = nested_dir / "Daily_20260618.csv.part"
    manifest = tmp_path / "download_manifest.json"
    write_text(top_level, "")
    write_text(nested, "")
    write_text(part, "")
    write_text(manifest, "{}")

    assert discover_taifex_files(tmp_path) == [top_level, nested]


def test_import_taifex_ticks_rejects_missing_required_columns(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    write_text(raw_dir / "ticks.csv", "symbol,price,volume\nTMF,22000,1\n")

    with pytest.raises(TaifexImportError, match="missing required TAIFEX columns"):
        import_taifex_ticks(raw_dir, tmp_path / "processed")


def test_import_taifex_ticks_rejects_non_tmf_symbol_argument(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    with pytest.raises(ValueError, match="TMF"):
        import_taifex_ticks(raw_dir, tmp_path / "processed", symbol="TX")


def test_import_taifex_ticks_empty_raw_directory_returns_empty_summary(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    summary = import_taifex_ticks(raw_dir, tmp_path / "processed")

    assert summary.files_discovered == 0
    assert summary.csv_files_read == 0
    assert summary.output_tick_count == 0
    assert summary.output_paths == ()
