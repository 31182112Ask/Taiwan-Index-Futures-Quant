from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest

from tifq.data.storage import read_parquet
from tifq.data.taifex_loader import (
    PARSER_VERSION,
    TaifexImportError,
    discover_taifex_files,
    import_taifex_ticks,
)


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")

def test_import_official_taifex_b_plus_s_volume_column(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    raw_dir.mkdir()

    csv_content = "\n".join(
        [
            (
                "成交日期,商品代號,到期月份(週別),成交時間,"
                "成交價格,成交數量(B+S),近月價格,遠月價格,開盤集合競價"
            ),
            "2026/05/11,TMF,202605,08:45:00,22000,2,,,",
        ]
    )

    with ZipFile(raw_dir / "Daily_2026_05_11.zip", "w") as archive:
        archive.writestr("Daily_2026_05_11.csv", csv_content)

    summary = import_taifex_ticks(raw_dir, processed_dir)

    assert summary.files_discovered == 1
    assert summary.csv_files_read == 1
    assert summary.output_tick_count == 1

    ticks = read_parquet(summary.output_paths[0])
    assert ticks.loc[0, "symbol"] == "TMF"
    assert ticks.loc[0, "volume"] == 2

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


def test_incremental_import_second_run_is_true_no_op(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    raw_dir.mkdir()
    write_text(
        raw_dir / "ticks.csv",
        "symbol,contract,timestamp,price,volume\n"
        "TMF,202606,2026-06-17 08:45:00,22000,1\n",
    )
    first = import_taifex_ticks(raw_dir, processed_dir)
    output = first.output_paths[0]
    output_mtime = output.stat().st_mtime_ns

    monkeypatch.setattr(
        "tifq.data.taifex_loader._read_raw_file",
        lambda path: (_ for _ in ()).throw(AssertionError(f"unexpected read: {path}")),
    )
    second = import_taifex_ticks(raw_dir, processed_dir)

    assert second.no_op is True
    assert second.files_skipped == 1
    assert second.csv_files_read == 0
    assert output.stat().st_mtime_ns == output_mtime
    assert (processed_dir / "import_manifest.json").exists()


def test_incremental_import_reads_only_new_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    raw_dir.mkdir()
    first_file = raw_dir / "first.csv"
    write_text(
        first_file,
        "symbol,contract,timestamp,price,volume\n"
        "TMF,202606,2026-06-17 08:45:00,22000,1\n",
    )
    import_taifex_ticks(raw_dir, processed_dir)
    second_file = raw_dir / "second.csv"
    write_text(
        second_file,
        "symbol,contract,timestamp,price,volume\n"
        "TMF,202606,2026-06-18 08:45:00,22100,1\n",
    )
    from tifq.data import taifex_loader

    original = taifex_loader._read_raw_file
    reads: list[Path] = []

    def recording_read(path: Path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(taifex_loader, "_read_raw_file", recording_read)

    summary = import_taifex_ticks(raw_dir, processed_dir)

    assert reads == [second_file.resolve()]
    assert summary.files_skipped == 1
    assert summary.files_changed == 1


def test_parser_version_change_rebuilds_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    raw_dir.mkdir()
    write_text(
        raw_dir / "ticks.csv",
        "symbol,contract,timestamp,price,volume\n"
        "TMF,202606,2026-06-17 08:45:00,22000,1\n",
    )
    import_taifex_ticks(raw_dir, processed_dir)
    monkeypatch.setattr("tifq.data.taifex_loader.PARSER_VERSION", PARSER_VERSION + "-new")

    summary = import_taifex_ticks(raw_dir, processed_dir)

    assert summary.files_changed == 1
    assert summary.no_op is False
