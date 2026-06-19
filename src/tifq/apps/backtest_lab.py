"""Streamlit Backtest Lab for V1 local research workflows."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, time
from importlib import import_module
from inspect import Parameter, signature
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from tifq.backtest import (
    BacktestResult,
    persist_backtest_result,
    run_backtest_from_config,
)
from tifq.backtest.contracts import select_contract_bars
from tifq.backtest.engine import discover_bar_files, load_configured_bars
from tifq.backtest.metrics import MetricValue
from tifq.bars import build_bar_files, discover_tick_files
from tifq.config import ConfigLoadError, load_backtest_config
from tifq.config.models import BacktestConfig
from tifq.data import import_taifex_ticks
from tifq.data.storage import read_parquet
from tifq.data.taifex_fetcher import (
    TaifexDownloadPlan,
    TaifexFetchError,
    TaifexFetchSummary,
    plan_recent_taifex_csv_files,
    sync_recent_taifex_csv_files,
)
from tifq.indicators import append_basic_indicators
from tifq.runtime import (
    CleanupSummary,
    HealthReport,
    apply_confirmed_cleanup,
    apply_safe_cleanup,
    run_environment_health_check,
)
from tifq.runtime.cleanup import CleanupAction
from tifq.runtime.progress import ProgressUpdate

_UNSAFE_ELEMENT_KEY_RE = re.compile(r"[^A-Za-z0-9_-]")


@dataclass(frozen=True)
class DataSummary:
    """Small tabular summary for local processed data."""

    file_count: int
    row_count: int
    start: str
    end: str
    contracts: str


@dataclass(frozen=True)
class ResultRun:
    """Discovered persisted backtest run."""

    strategy: str
    run_id: str
    run_dir: Path
    modified_time: float


@dataclass(frozen=True)
class LoadedResultRun:
    """Persisted run files loaded for browsing and comparison."""

    config: dict[str, Any]
    metrics: dict[str, MetricValue]
    trades: pd.DataFrame
    equity_curve: pd.DataFrame
    model_bars: pd.DataFrame
    signals: pd.DataFrame
    contract_selection: pd.DataFrame
    diagnostics: dict[str, Any]
    timings: dict[str, float]
    legacy: bool


def main() -> None:
    """Render the local Streamlit Backtest Lab."""
    st: Any = import_module("streamlit")
    try:
        go: Any | None = import_module("plotly.graph_objects")
    except ModuleNotFoundError:
        go = None

    st.set_page_config(
        page_title="TIFQ Backtest Lab",
        page_icon="TMF",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_style(st)
    st.title("Taiwan Index Futures Quant")
    startup_check = st.cache_resource(show_spinner=False)(_startup_environment_check)
    startup_report, startup_cleanup = startup_check(str(Path.cwd()))
    if "health_report" not in st.session_state:
        st.session_state["health_report"] = startup_report
        st.session_state["startup_cleanup"] = startup_cleanup
    _render_environment_status(st)

    config_path = st.sidebar.text_input("Config", "configs/v1_backtest.yaml")
    try:
        base_config = load_backtest_config(config_path)
    except (ConfigLoadError, ValueError) as exc:
        st.error(f"Config load failed: {exc}")
        return

    config = _render_sidebar_config(st, base_config)
    tabs = st.tabs(["Data Import", "Bar Builder", "Strategy Config", "Run Backtest", "Results"])

    with tabs[0]:
        _render_data_import(st, config)
    with tabs[1]:
        _render_bar_builder(st, config)
    with tabs[2]:
        _render_strategy_config(st, config)
    with tabs[3]:
        _render_run_backtest(st, go, config)
    with tabs[4]:
        _render_result_browser(st, go, config)


def discover_raw_files(raw_dir: str | Path) -> list[Path]:
    """Return sorted local TAIFEX raw files for UI preview."""
    raw_path = Path(raw_dir)
    if not raw_path.exists() or not raw_path.is_dir():
        return []
    return sorted(
        path
        for path in raw_path.rglob("*")
        if path.is_file() and path.suffix.lower() in {".csv", ".zip"}
    )


def summarize_ticks(processed_dir: str | Path, symbol: str = "TMF") -> DataSummary:
    """Summarize cleaned tick Parquet files."""
    tick_files = discover_tick_files(processed_dir, symbol)
    return _summarize_parquet_files(tick_files)


def summarize_bars(
    processed_dir: str | Path,
    *,
    symbol: str = "TMF",
    timeframe: str,
) -> DataSummary:
    """Summarize OHLCV bar Parquet files."""
    bar_dir = Path(processed_dir) / "bars" / symbol / timeframe
    if not bar_dir.exists() or not bar_dir.is_dir():
        return DataSummary(0, 0, "-", "-", "-")
    files = sorted(
        path for path in bar_dir.iterdir() if path.is_file() and path.suffix == ".parquet"
    )
    return _summarize_parquet_files(files)


def discover_result_runs(
    results_dir: str | Path = Path("data/results/backtests"),
) -> list[ResultRun]:
    """Discover persisted result runs under the V1 output layout."""
    root = Path(results_dir)
    if not root.exists() or not root.is_dir():
        return []

    runs: list[ResultRun] = []
    for strategy_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        for run_dir in sorted(path for path in strategy_dir.iterdir() if path.is_dir()):
            if (run_dir / "metrics.json").exists():
                runs.append(
                    ResultRun(
                        strategy=strategy_dir.name,
                        run_id=run_dir.name,
                        run_dir=run_dir,
                        modified_time=run_dir.stat().st_mtime,
                    )
                )
    return sorted(runs, key=lambda run: run.modified_time, reverse=True)


def load_result_run(
    run_dir: str | Path,
) -> LoadedResultRun:
    """Load config, metrics, trades, and equity curve from a persisted result run."""
    path = Path(run_dir)
    config_payload = yaml.safe_load((path / "config.yaml").read_text(encoding="utf-8"))
    if not isinstance(config_payload, dict):
        raise ValueError(f"result config must be a YAML mapping: {path / 'config.yaml'}")
    metrics = json.loads((path / "metrics.json").read_text(encoding="utf-8"))
    trades = pd.read_csv(path / "trades.csv")
    equity_curve = pd.read_csv(path / "equity_curve.csv")
    model_bars_path = path / "model_bars.parquet"
    signals_path = path / "signals.csv"
    contract_selection_path = path / "contract_selection.csv"
    diagnostics_path = path / "diagnostics.json"
    timings_path = path / "timings.json"
    required_new = (
        model_bars_path,
        signals_path,
        contract_selection_path,
        diagnostics_path,
        timings_path,
        path / "data_fingerprint.json",
    )
    return LoadedResultRun(
        config=config_payload,
        metrics=metrics,
        trades=trades,
        equity_curve=equity_curve,
        model_bars=read_parquet(model_bars_path) if model_bars_path.exists() else pd.DataFrame(),
        signals=_read_optional_csv(signals_path),
        contract_selection=_read_optional_csv(contract_selection_path),
        diagnostics=_read_optional_json(diagnostics_path),
        timings={
            key: float(value)
            for key, value in _read_optional_json(timings_path).items()
            if isinstance(value, int | float)
        },
        legacy=not all(artifact.exists() for artifact in required_new),
    )


def _read_optional_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _startup_environment_check(repository_root: str) -> tuple[HealthReport, CleanupSummary]:
    report = run_environment_health_check(repository_root)
    cleanup = apply_safe_cleanup(report.cleanup_plan, repository_root)
    if cleanup.applied:
        report = run_environment_health_check(repository_root)
    return report, cleanup


def _render_environment_status(st: Any) -> None:
    report = st.session_state.get("health_report")
    if not isinstance(report, HealthReport):
        return
    st.subheader("Environment Status")
    status_columns = st.columns(5)
    status_columns[0].metric("Status", report.status.title())
    status_columns[1].metric("Last checked", report.checked_at[11:19] + " UTC")
    startup_cleanup = st.session_state.get("startup_cleanup")
    cleaned = len(startup_cleanup.applied) if isinstance(startup_cleanup, CleanupSummary) else 0
    status_columns[2].metric("Temporary files cleaned", cleaned)
    status_columns[3].metric(
        "Duplicate candidates", report.cleanup_plan.confirmation_action_count
    )
    status_columns[4].metric(
        "Conflicts", sum(issue.severity == "error" for issue in report.issues)
    )
    controls = st.columns(3)
    if controls[0].button("Recheck", key="environment_recheck"):
        st.session_state["health_report"] = run_environment_health_check(Path.cwd())
        st.rerun()
    if controls[1].button("Full scan", key="environment_full_scan"):
        st.session_state["health_report"] = run_environment_health_check(
            Path.cwd(), full_scan=True
        )
        st.rerun()
    review = controls[2].toggle(
        "Review cleanup plan",
        value=False,
        key="environment_review_cleanup",
    )
    if review:
        plan = report.cleanup_plan
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "action": action.action,
                        "path": str(action.path),
                        "reason": action.reason,
                        "bytes": action.size_bytes,
                        "automatic": action.safe_to_apply_automatically,
                    }
                    for action in plan.actions
                ]
            ),
            width="stretch",
            hide_index=True,
            key="environment_cleanup_plan",
        )
        action_columns = st.columns(2)
        if action_columns[0].button(
            "Apply safe cleanup",
            disabled=plan.safe_action_count == 0,
            key="environment_apply_safe",
        ):
            summary = apply_safe_cleanup(plan, Path.cwd())
            st.session_state["startup_cleanup"] = summary
            st.session_state["health_report"] = run_environment_health_check(Path.cwd())
            st.rerun()
        confirm = action_columns[1].checkbox(
            "Confirm duplicate quarantine",
            key="environment_confirm_quarantine",
        )
        duplicate_actions = tuple(
            action
            for action in plan.actions
            if action.action == "quarantine" and "duplicate raw content" in action.reason
        )
        if st.button(
            "Quarantine confirmed duplicates",
            disabled=not confirm or not duplicate_actions,
            key="environment_quarantine_duplicates",
        ):
            apply_confirmed_cleanup(duplicate_actions, Path.cwd())
            st.session_state["health_report"] = run_environment_health_check(
                Path.cwd(), full_scan=True
            )
            st.rerun()


def build_run_comparison_table(runs: list[tuple[ResultRun, LoadedResultRun]]) -> pd.DataFrame:
    """Build a compact parameter and metric comparison table for 2-5 result runs."""
    records = [_comparison_record(run, loaded) for run, loaded in runs]
    return pd.DataFrame(
        records,
        columns=[
            "run_id",
            "date_range",
            "timeframe",
            "ema_fast",
            "ema_slow",
            "atr_period",
            "atr_stop_mult",
            "take_profit_r",
            "commission",
            "slippage",
            "net_pnl",
            "max_drawdown",
            "win_rate",
            "profit_factor",
            "trade_count",
        ],
    )


def build_config_override(
    base_config: BacktestConfig,
    *,
    raw_dir: Path,
    processed_dir: Path,
    start_date: date,
    end_date: date,
    timeframe: str,
    ema_fast: int,
    ema_slow: int,
    atr_period: int,
    atr_stop_mult: float,
    take_profit_r: float,
    min_atr_points: float,
    max_atr_points: float,
    max_trades_per_day: int,
    force_flatten_time: time,
    no_entry_before: time,
    no_entry_after: time,
    commission_per_side: float,
    tax_rate: float,
    slippage_points_per_side: float,
    initial_cash: float,
    max_position: int,
    allow_short: bool,
    contract_mode: str | None = None,
    contract: str | None = None,
    roll_confirmation_days: int | None = None,
    assumed_margin_per_contract: float | None = None,
) -> BacktestConfig:
    """Build a validated config from UI control values."""
    payload = base_config.model_dump(mode="python")
    payload["data"] = {
        **payload["data"],
        "raw_dir": raw_dir,
        "processed_dir": processed_dir,
        "start_date": start_date,
        "end_date": end_date,
        "timeframe": timeframe,
        "contract_mode": contract_mode or base_config.data.contract_mode,
        "contract": contract,
        "roll_confirmation_days": roll_confirmation_days
        if roll_confirmation_days is not None
        else base_config.data.roll_confirmation_days,
    }
    payload["strategy"] = {
        **payload["strategy"],
        "params": {
            **payload["strategy"]["params"],
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "atr_period": atr_period,
            "atr_stop_mult": atr_stop_mult,
            "take_profit_r": take_profit_r,
            "min_atr_points": min_atr_points,
            "max_atr_points": max_atr_points,
            "max_trades_per_day": max_trades_per_day,
            "force_flatten_time": force_flatten_time.isoformat(),
            "no_entry_before": no_entry_before.isoformat(),
            "no_entry_after": no_entry_after.isoformat(),
        },
    }
    payload["cost"] = {
        **payload["cost"],
        "commission_per_side": commission_per_side,
        "tax_rate": tax_rate,
        "slippage_points_per_side": slippage_points_per_side,
    }
    payload["portfolio"] = {
        **payload["portfolio"],
        "initial_cash": initial_cash,
        "max_position": max_position,
        "allow_short": allow_short,
        "assumed_margin_per_contract": assumed_margin_per_contract,
    }
    return BacktestConfig.model_validate(payload)


def _render_sidebar_config(st: Any, base_config: BacktestConfig) -> BacktestConfig:
    data = base_config.data
    strategy_params: dict[str, object] = dict(base_config.strategy.params)
    cost = base_config.cost
    portfolio = base_config.portfolio

    st.sidebar.header("Run Settings")
    raw_dir = Path(st.sidebar.text_input("Raw data directory", str(data.raw_dir)))
    processed_dir = Path(st.sidebar.text_input("Processed directory", str(data.processed_dir)))
    start_date = st.sidebar.date_input("Start date", data.start_date)
    end_date = st.sidebar.date_input("End date", data.end_date)
    timeframe = st.sidebar.radio(
        "Timeframe",
        options=["5m", "1m"],
        index=["5m", "1m"].index(data.timeframe),
        horizontal=True,
    )
    contract_mode = st.sidebar.selectbox(
        "Contract mode",
        options=["continuous_front_month", "single_contract"],
        index=["continuous_front_month", "single_contract"].index(data.contract_mode),
    )
    contract = None
    if contract_mode == "single_contract":
        contract = st.sidebar.text_input("Contract (YYYYMM)", data.contract or "") or None
    roll_confirmation_days = st.sidebar.number_input(
        "Roll confirmation days",
        min_value=1,
        value=int(data.roll_confirmation_days),
        disabled=contract_mode == "single_contract",
    )

    st.sidebar.header("Strategy")
    ema_fast = st.sidebar.number_input(
        "EMA fast",
        min_value=1,
        value=_int_param(strategy_params, "ema_fast", 20),
    )
    ema_slow = st.sidebar.number_input(
        "EMA slow",
        min_value=1,
        value=_int_param(strategy_params, "ema_slow", 60),
    )
    atr_period = st.sidebar.number_input(
        "ATR period",
        min_value=1,
        value=_int_param(strategy_params, "atr_period", 14),
    )
    atr_stop_mult = st.sidebar.number_input(
        "ATR stop multiplier",
        min_value=0.0,
        value=_float_param(strategy_params, "atr_stop_mult", 1.5),
        step=0.1,
    )
    take_profit_r = st.sidebar.number_input(
        "Take profit R",
        min_value=0.0,
        value=_float_param(strategy_params, "take_profit_r", 1.5),
        step=0.1,
    )
    min_atr_points = st.sidebar.number_input(
        "Minimum ATR",
        min_value=0.0,
        value=_float_param(strategy_params, "min_atr_points", 10.0),
        step=1.0,
    )
    max_atr_points = st.sidebar.number_input(
        "Maximum ATR",
        min_value=0.0,
        value=_float_param(strategy_params, "max_atr_points", 120.0),
        step=1.0,
    )
    max_trades_per_day = st.sidebar.number_input(
        "Max trades per day",
        min_value=0,
        value=_int_param(strategy_params, "max_trades_per_day", 3),
    )

    force_flatten_time = st.sidebar.time_input(
        "Force flatten",
        _time_param(strategy_params, "force_flatten_time", time(13, 35)),
    )
    no_entry_before = st.sidebar.time_input(
        "No entry before",
        _time_param(strategy_params, "no_entry_before", time(8, 55)),
    )
    no_entry_after = st.sidebar.time_input(
        "No entry after",
        _time_param(strategy_params, "no_entry_after", time(13, 20)),
    )

    st.sidebar.header("Costs")
    commission_per_side = st.sidebar.number_input(
        "Commission per side",
        min_value=0.0,
        value=float(cost.commission_per_side),
        step=1.0,
    )
    tax_rate = st.sidebar.number_input(
        "Tax rate",
        min_value=0.0,
        value=float(cost.tax_rate),
        format="%.8f",
        step=0.00001,
    )
    slippage_points_per_side = st.sidebar.number_input(
        "Slippage points per side",
        min_value=0.0,
        value=float(cost.slippage_points_per_side),
        step=1.0,
    )

    st.sidebar.header("Portfolio")
    initial_cash = st.sidebar.number_input(
        "Initial accounting cash",
        min_value=1.0,
        value=float(portfolio.initial_cash),
        step=10_000.0,
    )
    max_position = st.sidebar.number_input(
        "Max position",
        min_value=0,
        max_value=1,
        value=int(portfolio.max_position),
    )
    allow_short = st.sidebar.checkbox("Allow short", value=portfolio.allow_short)
    use_assumed_margin = st.sidebar.checkbox(
        "Use assumed margin",
        value=portfolio.assumed_margin_per_contract is not None,
    )
    assumed_margin = st.sidebar.number_input(
        "Assumed margin per contract",
        min_value=1.0,
        value=float(portfolio.assumed_margin_per_contract or 50_000.0),
        disabled=not use_assumed_margin,
        help="User assumption only; this is not a live official TAIFEX margin value.",
    )
    st.sidebar.caption(
        "Initial cash sets starting equity only. V1 sizes fixed contracts through max position."
    )

    return build_config_override(
        base_config,
        raw_dir=raw_dir,
        processed_dir=processed_dir,
        start_date=start_date,
        end_date=end_date,
        timeframe=str(timeframe),
        ema_fast=int(ema_fast),
        ema_slow=int(ema_slow),
        atr_period=int(atr_period),
        atr_stop_mult=float(atr_stop_mult),
        take_profit_r=float(take_profit_r),
        min_atr_points=float(min_atr_points),
        max_atr_points=float(max_atr_points),
        max_trades_per_day=int(max_trades_per_day),
        force_flatten_time=force_flatten_time,
        no_entry_before=no_entry_before,
        no_entry_after=no_entry_after,
        commission_per_side=float(commission_per_side),
        tax_rate=float(tax_rate),
        slippage_points_per_side=float(slippage_points_per_side),
        initial_cash=float(initial_cash),
        max_position=int(max_position),
        allow_short=bool(allow_short),
        contract_mode=str(contract_mode),
        contract=contract,
        roll_confirmation_days=int(roll_confirmation_days),
        assumed_margin_per_contract=float(assumed_margin) if use_assumed_margin else None,
    )


def _render_data_import(st: Any, config: BacktestConfig) -> None:
    st.subheader("Data Import")
    _render_official_sync(st, config)
    raw_files = discover_raw_files(config.data.raw_dir)
    tick_summary = summarize_ticks(config.data.processed_dir, config.data.symbol)

    cols = st.columns(4)
    cols[0].metric("Raw files", len(raw_files))
    cols[1].metric("Tick files", tick_summary.file_count)
    cols[2].metric("Ticks", f"{tick_summary.row_count:,}")
    cols[3].metric("Range", _date_range_label(tick_summary))

    if raw_files:
        st.dataframe(
            pd.DataFrame({"file": [str(path) for path in raw_files]}),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No local TAIFEX CSV or ZIP files found in the selected raw directory.")

    if st.button("Import TAIFEX", type="primary"):
        progress = _StreamlitProgress(st, "Importing TAIFEX files")
        try:
            summary = import_taifex_ticks(
                config.data.raw_dir,
                config.data.processed_dir,
                progress_callback=progress,
            )
        except (OSError, ValueError) as exc:
            st.error(f"Import failed: {exc}")
        else:
            st.success("Import completed.")
            st.json(
                {
                    "files_discovered": summary.files_discovered,
                    "csv_files_read": summary.csv_files_read,
                    "input_rows": summary.input_row_count,
                    "clean_ticks": summary.output_tick_count,
                    "invalid_or_filtered_rows": summary.invalid_row_count,
                    "output_paths": [str(path) for path in summary.output_paths],
                    "unchanged_files": summary.files_skipped,
                    "changed_files": summary.files_changed,
                    "no_op": summary.no_op,
                }
            )


def _render_official_sync(st: Any, config: BacktestConfig) -> None:
    st.write("Official TAIFEX Sync")
    sync_cols = st.columns(3)
    limit = sync_cols[0].number_input(
        "Number of trading days",
        min_value=1,
        max_value=30,
        value=30,
    )
    overwrite = sync_cols[1].checkbox("Overwrite existing files", value=False)
    sync_cols[2].metric("Selected bar timeframe", config.data.timeframe)

    button_cols = st.columns(2)
    download_only = button_cols[0].button("Sync downloads only")
    full_sync = button_cols[1].button("Sync, import, and build bars", type="primary")
    if download_only or full_sync:
        progress = _StreamlitProgress(st, "Planning official TAIFEX sync")
        try:
            plan = plan_recent_taifex_csv_files(
                config.data.raw_dir,
                limit=int(limit),
                progress_callback=progress,
            )
        except (OSError, ValueError, TaifexFetchError) as exc:
            st.error(f"TAIFEX plan failed: {exc}")
            return
        st.session_state["taifex_download_plan"] = plan
        st.session_state["taifex_full_sync"] = bool(full_sync)

    plan = st.session_state.get("taifex_download_plan")
    if not isinstance(plan, TaifexDownloadPlan):
        return
    st.write("Download Plan")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "trading_date": item.remote.trading_date,
                    "remote_filename": item.remote.remote_filename,
                    "local_path": str(item.local_path),
                    "status": item.status,
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                    "recommended_action": item.recommended_action,
                }
                for item in plan.items
            ]
        ),
        width="stretch",
        hide_index=True,
        key="taifex_download_plan_table",
    )
    st.caption(
        f"{plan.valid_existing_count} valid existing, {plan.missing_count} missing/changed, "
        f"{plan.conflict_count} conflicts."
    )
    if plan.conflict_count:
        st.error("Conflicting or corrupt local files require review before download can continue.")
        confirm_quarantine = st.checkbox(
            "Confirm moving listed conflicts to data/quarantine",
            key="taifex_confirm_conflict_quarantine",
        )
        conflict_columns = st.columns(2)
        if conflict_columns[0].button("Cancel", key="taifex_conflict_cancel"):
            _clear_download_plan(st)
            st.rerun()
        if conflict_columns[1].button(
            "Quarantine conflicts",
            disabled=not confirm_quarantine,
            key="taifex_quarantine_conflicts",
        ):
            actions = tuple(
                CleanupAction(
                    "quarantine",
                    item.local_path,
                    f"TAIFEX {item.status}",
                    item.size_bytes or 0,
                    False,
                )
                for item in plan.items
                if item.status in {"unmanaged_conflict", "corrupt_existing"}
            )
            summary = apply_confirmed_cleanup(actions, Path.cwd())
            if summary.failed:
                st.error("; ".join(summary.failed))
            else:
                st.success(f"Quarantined {len(summary.applied)} conflict files.")
                _clear_download_plan(st)
        return

    if plan.no_download_required:
        st.info(
            f"Requested TAIFEX data already exists: {len(plan.items)} of {len(plan.items)} "
            "selected trading days are valid."
        )
        primary_label = "Use existing files"
    else:
        st.info(
            f"{plan.valid_existing_count} valid existing and "
            f"{plan.missing_count} files to download."
        )
        primary_label = "Download missing files only"
    decision_columns = st.columns(2)
    if decision_columns[0].button("Cancel", key="taifex_plan_cancel"):
        _clear_download_plan(st)
        st.rerun()
    proceed = decision_columns[1].button(primary_label, type="primary", key="taifex_plan_proceed")
    force_confirmed = st.checkbox(
        "I confirm overwriting every selected managed file",
        value=False,
        key="taifex_force_confirm",
    )
    force = st.button(
        "Force redownload all",
        disabled=not (force_confirmed and overwrite),
        key="taifex_force_redownload",
    )
    if not (proceed or force):
        return
    _execute_official_sync(
        st,
        config,
        limit=int(limit),
        overwrite=bool(force),
        full_sync=bool(st.session_state.get("taifex_full_sync", False)),
    )
    _clear_download_plan(st)


def _execute_official_sync(
    st: Any,
    config: BacktestConfig,
    *,
    limit: int,
    overwrite: bool,
    full_sync: bool,
) -> None:
    progress = _StreamlitProgress(st, "Syncing official TAIFEX files")
    try:
        fetch_summary = sync_recent_taifex_csv_files(
            config.data.raw_dir,
            limit=limit,
            overwrite=overwrite,
            progress_callback=progress,
        )
        if fetch_summary.files_failed:
            st.error("Official TAIFEX sync completed with failures.")
            st.json(_sync_display_payload(fetch_summary, None, None))
            return
        import_summary = None
        bar_summary = None
        if full_sync:
            import_summary = import_taifex_ticks(
                config.data.raw_dir,
                config.data.processed_dir,
                symbol=config.data.symbol,
                progress_callback=progress,
            )
            bar_summary = build_bar_files(
                config.data.processed_dir,
                symbol=config.data.symbol,
                timeframe=config.data.timeframe,
                progress_callback=progress,
            )
    except (OSError, ValueError, TaifexFetchError) as exc:
        st.error(f"TAIFEX sync failed: {exc}")
        return
    st.success("Official TAIFEX sync completed.")
    st.json(_sync_display_payload(fetch_summary, import_summary, bar_summary))


def _clear_download_plan(st: Any) -> None:
    st.session_state.pop("taifex_download_plan", None)
    st.session_state.pop("taifex_full_sync", None)


def _sync_display_payload(
    fetch_summary: TaifexFetchSummary,
    import_summary: Any | None,
    bar_summary: Any | None,
) -> dict[str, Any]:
    dates = [record.trading_date for record in fetch_summary.records]
    payload: dict[str, Any] = {
        "remote_files_discovered": fetch_summary.files_discovered,
        "files_selected": fetch_summary.files_selected,
        "downloaded": fetch_summary.files_downloaded,
        "skipped": fetch_summary.files_skipped,
        "updated": fetch_summary.files_updated,
        "failed": fetch_summary.files_failed,
        "failures": [
            {
                "trading_date": failure.trading_date.isoformat(),
                "local_path": str(failure.local_path),
                "error": failure.error,
            }
            for failure in fetch_summary.failures
        ],
        "latest_available_trading_date": max(dates).isoformat() if dates else "-",
        "earliest_available_trading_date": min(dates).isoformat() if dates else "-",
        "download_paths": [str(record.local_path) for record in fetch_summary.records],
    }
    if import_summary is not None:
        payload["imported_tick_count"] = import_summary.output_tick_count
        payload["filtered_invalid_tick_count"] = import_summary.invalid_row_count
    if bar_summary is not None:
        payload["built_bar_count"] = bar_summary.output_bar_count
    return payload


def _render_bar_builder(st: Any, config: BacktestConfig) -> None:
    st.subheader("Bar Builder")
    summary = summarize_bars(
        config.data.processed_dir,
        symbol=config.data.symbol,
        timeframe=config.data.timeframe,
    )
    cols = st.columns(4)
    cols[0].metric("Bar files", summary.file_count)
    cols[1].metric("Bars", f"{summary.row_count:,}")
    cols[2].metric("Range", _date_range_label(summary))
    cols[3].metric("Contracts", summary.contracts)
    manifest_path = config.data.processed_dir / "bar_manifest.json"
    st.caption(
        f"Incremental manifest: {'available' if manifest_path.exists() else 'not created yet'} "
        f"({manifest_path})"
    )
    tick_files = discover_tick_files(config.data.processed_dir, config.data.symbol)
    st.caption(f"Incremental candidates: {len(tick_files)} daily tick files.")
    force_rebuild = st.checkbox(
        "Force rebuild selected dates",
        value=False,
        key="bar_builder_force_rebuild",
        help="Default builds only changed tick files. Force rebuild rewrites all selected dates.",
    )
    confirm_force = st.checkbox(
        "Confirm forced bar rebuild",
        value=False,
        disabled=not force_rebuild,
        key="bar_builder_confirm_force",
    )

    if st.button(
        "Build bars",
        type="primary",
        disabled=force_rebuild and not confirm_force,
    ):
        progress = _StreamlitProgress(st, "Building OHLCV bars")
        try:
            build_summary = build_bar_files(
                config.data.processed_dir,
                symbol=config.data.symbol,
                timeframe=config.data.timeframe,
                force=force_rebuild,
                progress_callback=progress,
            )
        except (OSError, ValueError) as exc:
            st.error(f"Bar build failed: {exc}")
        else:
            st.success("Bar build completed.")
            st.json(
                {
                    "tick_files_read": build_summary.tick_files_read,
                    "input_ticks": build_summary.input_tick_count,
                    "output_bars": build_summary.output_bar_count,
                    "output_paths": [str(path) for path in build_summary.output_paths],
                    "unchanged_tick_files": build_summary.tick_files_skipped,
                    "rebuilt_tick_files": build_summary.tick_files_rebuilt,
                    "no_op": build_summary.no_op,
                }
            )

    preview = _load_bar_preview(
        config.data.processed_dir,
        config.data.symbol,
        config.data.timeframe,
    )
    if not preview.empty:
        st.dataframe(preview.tail(200), width="stretch", hide_index=True)


def _render_strategy_config(st: Any, config: BacktestConfig) -> None:
    st.subheader("Strategy Config")
    left, right = st.columns(2)
    left.write("Strategy")
    left.json(config.strategy.model_dump(mode="json"))
    right.write("Costs and Portfolio")
    right.json(
        {
            "cost": config.cost.model_dump(mode="json"),
            "portfolio": config.portfolio.model_dump(mode="json"),
        }
    )
    st.write("Contract and Position Model")
    st.json(
        {
            "contract_mode": config.data.contract_mode,
            "single_contract": config.data.contract,
            "roll_confirmation_days": config.data.roll_confirmation_days,
            "continuous_series": "unadjusted",
            "position_sizing_mode": "fixed contracts",
            "maximum_position": f"{config.portfolio.max_position} contract",
            "initial_cash_role": "starting accounting equity only",
            "margin_validation": (
                f"user assumption: {config.portfolio.assumed_margin_per_contract:,.0f}"
                if config.portfolio.assumed_margin_per_contract is not None
                else "not configured"
            ),
        }
    )


def _render_run_backtest(st: Any, go: Any, config: BacktestConfig) -> None:
    st.subheader("Run Backtest")
    fingerprint = _configured_bar_fingerprint(config)
    cached_preflight = st.cache_data(show_spinner=False)(_backtest_preflight)
    try:
        preflight = cached_preflight(config.model_dump_json(), fingerprint)
        preflight_error = None
    except (OSError, ValueError) as exc:
        preflight = {}
        preflight_error = str(exc)
    st.write("Data Health")
    if preflight_error is not None:
        st.error(preflight_error)
    else:
        preflight_columns = st.columns(6)
        preflight_columns[0].metric("Estimated bars", preflight.get("bar_count", 0))
        preflight_columns[1].metric("Trading days", preflight.get("trading_days", 0))
        preflight_columns[2].metric("Selected contracts", preflight.get("contracts", 0))
        preflight_columns[3].metric("Roll count", preflight.get("roll_count", 0))
        preflight_columns[4].metric("Duplicate timestamps", preflight.get("duplicates", 0))
        preflight_columns[5].metric("Indicator readiness", preflight.get("readiness", "-"))
        st.caption(
            f"Date range: {preflight.get('date_range', '-')}; mode: {config.data.contract_mode}; "
            "continuous series is unadjusted."
        )
    if st.button("Run backtest", type="primary", disabled=preflight_error is not None):
        progress = _StreamlitProgress(st, "Running backtest")
        try:
            result = run_backtest_from_config(config, progress_callback=progress)
            paths = persist_backtest_result(config, result, progress_callback=progress)
        except (OSError, ValueError) as exc:
            st.error(f"Backtest failed: {exc}")
        else:
            st.session_state["last_result"] = result
            st.session_state["last_run_dir"] = str(paths.run_dir)
            st.success(f"Backtest saved to {paths.run_dir}")

    result = st.session_state.get("last_result")
    if isinstance(result, BacktestResult):
        _render_result_summary(st, result.metrics)
        _render_charts(
            st,
            go,
            result.equity_curve,
            result.trades,
            result.model_bars.tail(500),
            key_prefix="run_backtest",
        )
        st.dataframe(
            result.trades,
            width="stretch",
            hide_index=True,
            key="run_backtest_trades",
        )
        st.caption(f"Latest run directory: {st.session_state.get('last_run_dir', '-')}")
        _render_backtest_diagnostics(st, result.diagnostics, result.metrics)
    else:
        st.info("Run a backtest to view equity, daily PnL, K-line overlays, and trades.")


def _configured_bar_fingerprint(config: BacktestConfig) -> tuple[tuple[str, int, int], ...]:
    files = discover_bar_files(
        config.data.processed_dir,
        symbol=config.data.symbol,
        timeframe=config.data.timeframe,
        start_date=config.data.start_date,
        end_date=config.data.end_date,
    )
    return tuple(
        (str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns) for path in files
    )


def _backtest_preflight(
    config_json: str,
    fingerprint: tuple[tuple[str, int, int], ...],
) -> dict[str, Any]:
    del fingerprint
    config = BacktestConfig.model_validate_json(config_json)
    raw_bars = load_configured_bars(config)
    selection = select_contract_bars(
        raw_bars,
        contract_mode=config.data.contract_mode,
        contract=config.data.contract,
        roll_confirmation_days=config.data.roll_confirmation_days,
    )
    params = config.strategy.params
    enriched = append_basic_indicators(
        selection.bars,
        ema_fast=_int_param(dict(params), "ema_fast", 20),
        ema_slow=_int_param(dict(params), "ema_slow", 60),
        atr_period=_int_param(dict(params), "atr_period", 14),
        volatility_window=_int_param(dict(params), "volatility_window", 20),
    )
    timestamps = pd.to_datetime(selection.bars["timestamp"])
    readiness = enriched[["ema_fast", "ema_slow", "atr"]].notna().all(axis=1).mean()
    return {
        "bar_count": len(selection.bars),
        "trading_days": int(timestamps.dt.date.nunique()),
        "contracts": int(selection.bars["contract"].nunique()),
        "roll_count": int(selection.audit["rolled"].sum()),
        "duplicates": int(timestamps.duplicated().sum()),
        "readiness": f"{readiness:.1%}",
        "date_range": f"{timestamps.min().date()} to {timestamps.max().date()}",
    }


def _render_result_browser(st: Any, go: Any, config: BacktestConfig) -> None:
    st.subheader("Result Browser")
    runs = discover_result_runs(config.data.processed_dir.parent / "results" / "backtests")
    if not runs:
        st.info("No persisted backtest runs found.")
        return

    labels = [f"{run.strategy} / {run.run_id}" for run in runs]
    selected_label = st.selectbox("Run", labels)
    selected = runs[labels.index(selected_label)]
    cached_load = st.cache_data(show_spinner=False)(_load_result_run_cached)
    loaded = cached_load(str(selected.run_dir), _run_dir_fingerprint(selected.run_dir))
    result_key = _element_key(f"result_browser_{selected.run_id}")
    _render_result_summary(st, loaded.metrics)
    if loaded.legacy:
        st.warning(
            "Legacy run: reproducibility artifacts are incomplete. Existing metrics and trades "
            "remain available; missing diagnostics are not reconstructed."
        )
    _render_charts(
        st,
        go,
        loaded.equity_curve,
        loaded.trades,
        loaded.model_bars.tail(500),
        key_prefix=result_key,
    )
    st.dataframe(
        loaded.trades,
        width="stretch",
        hide_index=True,
        key=f"{result_key}_trades",
    )
    st.caption(str(selected.run_dir))
    if not loaded.contract_selection.empty:
        with st.expander("Contract Selection Audit"):
            st.dataframe(loaded.contract_selection, width="stretch", hide_index=True)
    if loaded.diagnostics:
        with st.expander("Diagnostics", expanded=loaded.metrics.get("trade_count", 0) == 0):
            st.json(loaded.diagnostics)
    if loaded.timings:
        with st.expander("Timing Summary"):
            st.json(loaded.timings)

    st.write("Run Comparison")
    default_labels = labels[: min(2, len(labels))]
    selected_labels = st.multiselect(
        "Compare runs",
        labels,
        default=default_labels,
        max_selections=5,
    )
    if len(selected_labels) < 2:
        st.info("Select 2 to 5 runs for comparison.")
        return
    selected_runs = [runs[labels.index(label)] for label in selected_labels[:5]]
    loaded_runs = [
        (
            run,
            cached_load(str(run.run_dir), _run_dir_fingerprint(run.run_dir)),
        )
        for run in selected_runs
    ]
    st.dataframe(
        build_run_comparison_table(loaded_runs),
        width="stretch",
        hide_index=True,
        key="result_comparison_table",
    )


def _render_result_summary(st: Any, metrics: dict[str, MetricValue]) -> None:
    cols = st.columns(6)
    cols[0].metric("Final equity", _money(metrics.get("final_equity", 0)))
    cols[1].metric("Net PnL", _money(metrics.get("net_pnl", 0)))
    cols[2].metric("Return", _pct(metrics.get("return_pct", 0)))
    cols[3].metric("Max DD", _money(metrics.get("max_drawdown", 0)))
    cols[4].metric("Win rate", _pct(metrics.get("win_rate", 0)))
    cols[5].metric("Trades", str(metrics.get("trade_count", 0)))

    detail_cols = st.columns(5)
    detail_cols[0].metric("Profit factor", f"{float(metrics.get('profit_factor', 0)):.2f}")
    detail_cols[1].metric("Expectancy", _money(metrics.get("expectancy", 0)))
    detail_cols[2].metric("Fee", _money(metrics.get("total_fee", 0)))
    detail_cols[3].metric("Tax", _money(metrics.get("total_tax", 0)))
    detail_cols[4].metric("Slippage", _money(metrics.get("total_slippage", 0)))


def _render_backtest_diagnostics(
    st: Any,
    diagnostics: dict[str, Any],
    metrics: dict[str, MetricValue],
) -> None:
    if not diagnostics:
        return
    stats = diagnostics.get("stats")
    if not isinstance(stats, dict):
        stats = {}
    st.write("Data Health")
    columns = st.columns(5)
    columns[0].metric("Bars", stats.get("bar_count", 0))
    columns[1].metric("Trading days", stats.get("trading_days", 0))
    columns[2].metric("Contracts", len(stats.get("selected_contracts", [])))
    columns[3].metric("Rolls", stats.get("roll_count", 0))
    columns[4].metric("Duplicate timestamps", stats.get("duplicate_timestamp_count", 0))
    st.caption(
        f"Indicator readiness: EMA {float(stats.get('ema_valid_ratio', 0)):.1%}, "
        f"ATR {float(stats.get('atr_valid_ratio', 0)):.1%}."
    )
    if int(metrics.get("trade_count", 0)) == 0:
        st.warning("No trades were generated.")
        st.json(
            {
                "bars_loaded": stats.get("bar_count", 0),
                "trading_days": stats.get("trading_days", 0),
                "contracts_before_selection": len(stats.get("original_contracts", [])),
                "contracts_after_selection": len(stats.get("selected_contracts", [])),
                "segments": stats.get("segment_count", 0),
                "bars_inside_entry_window": stats.get("entry_window_bars", 0),
                "atr_valid_ratio": stats.get("atr_valid_ratio", 0),
                "atr_in_range_ratio": stats.get("atr_in_range_ratio", 0),
                "long_candidates": stats.get("long_entry_candidates", 0),
                "short_candidates": stats.get("short_entry_candidates", 0),
                "buy_signals": stats.get("buy_signals", 0),
                "sell_signals": stats.get("sell_signals", 0),
                "primary_reason": diagnostics.get("primary_zero_trade_reason"),
            }
        )


class _StreamlitProgress:
    """Throttled-enough per-item renderer for framework-neutral progress callbacks."""

    def __init__(self, st: Any, label: str) -> None:
        self.status = st.status(label, expanded=True)
        self.progress = st.progress(0.0, text="Calculating...")

    def __call__(self, update: ProgressUpdate) -> None:
        percent = update.percent
        eta = (
            f"{update.eta_seconds:.1f}s"
            if update.eta_seconds is not None
            else "Calculating..."
        )
        count = (
            f"{update.completed} / {update.total}"
            if update.total is not None
            else str(update.completed)
        )
        text = (
            f"{update.phase}: {count} | elapsed {update.elapsed_seconds:.1f}s | "
            f"ETA {eta}"
        )
        if percent is not None:
            self.progress.progress(percent, text=text)
        else:
            self.status.write(text)
        self.status.write(update.message)
        if update.phase == "Complete":
            self.status.update(label=update.message, state="complete")


def _comparison_record(run: ResultRun, loaded: LoadedResultRun) -> dict[str, object]:
    config = loaded.config
    data = _mapping(config.get("data"))
    strategy = _mapping(config.get("strategy"))
    params = _mapping(strategy.get("params"))
    cost = _mapping(config.get("cost"))
    metrics = loaded.metrics

    start_date = str(data.get("start_date", "-"))
    end_date = str(data.get("end_date", "-"))
    return {
        "run_id": run.run_id,
        "date_range": f"{start_date} to {end_date}",
        "timeframe": data.get("timeframe", "-"),
        "ema_fast": params.get("ema_fast", "-"),
        "ema_slow": params.get("ema_slow", "-"),
        "atr_period": params.get("atr_period", "-"),
        "atr_stop_mult": params.get("atr_stop_mult", "-"),
        "take_profit_r": params.get("take_profit_r", "-"),
        "commission": cost.get("commission_per_side", "-"),
        "slippage": cost.get("slippage_points_per_side", "-"),
        "net_pnl": metrics.get("net_pnl", 0),
        "max_drawdown": metrics.get("max_drawdown", 0),
        "win_rate": metrics.get("win_rate", 0),
        "profit_factor": metrics.get("profit_factor", 0),
        "trade_count": metrics.get("trade_count", 0),
    }


def _mapping(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _run_dir_fingerprint(run_dir: Path) -> tuple[tuple[str, int, int], ...]:
    return tuple(
        (path.name, path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(run_dir.iterdir())
        if path.is_file()
    )


def _load_result_run_cached(
    run_dir: str,
    fingerprint: tuple[tuple[str, int, int], ...],
) -> LoadedResultRun:
    del fingerprint
    return load_result_run(run_dir)


def _render_charts(
    st: Any,
    go: Any | None,
    equity_curve: pd.DataFrame,
    trades: pd.DataFrame,
    chart_bars: pd.DataFrame,
    *,
    key_prefix: str,
) -> None:
    safe_prefix = _element_key(key_prefix)
    if go is not None:
        st.plotly_chart(
            _equity_figure(go, equity_curve),
            width="stretch",
            key=f"{safe_prefix}_equity_curve",
        )
        st.plotly_chart(
            _daily_pnl_figure(go, trades),
            width="stretch",
            key=f"{safe_prefix}_daily_pnl",
        )
        if not chart_bars.empty:
            st.plotly_chart(
                _kline_figure(go, chart_bars, trades),
                width="stretch",
                key=f"{safe_prefix}_kline",
            )
        return

    st.warning("Plotly is not installed in this environment; using Streamlit native charts.")
    if not equity_curve.empty and {"timestamp", "equity"}.issubset(equity_curve.columns):
        st.write("Equity Curve")
        _render_native_chart(
            st,
            "line_chart",
            equity_curve.set_index("timestamp")["equity"],
            key=f"{safe_prefix}_native_equity",
        )
    if not trades.empty and {"exit_time", "net_pnl"}.issubset(trades.columns):
        working = trades.copy()
        working["date"] = pd.to_datetime(working["exit_time"]).dt.date
        daily = working.groupby("date")["net_pnl"].sum()
        st.write("Daily PnL")
        _render_native_chart(
            st,
            "bar_chart",
            daily,
            key=f"{safe_prefix}_native_daily_pnl",
        )
    if not chart_bars.empty:
        columns = [
            column
            for column in ("close", "vwap", "ema_fast", "ema_slow")
            if column in chart_bars.columns
        ]
        if columns:
            st.write("Price and Indicators")
            _render_native_chart(
                st,
                "line_chart",
                chart_bars.set_index("timestamp")[columns],
                key=f"{safe_prefix}_native_indicators",
            )


def _element_key(value: str) -> str:
    """Return a deterministic Streamlit key containing only safe characters."""
    sanitized = _UNSAFE_ELEMENT_KEY_RE.sub("_", value)
    return sanitized or "element"


def _render_native_chart(
    st: Any,
    chart_name: str,
    data: pd.Series[Any] | pd.DataFrame,
    *,
    key: str,
) -> None:
    chart = getattr(st, chart_name)
    parameters = signature(chart).parameters.values()
    supports_key = any(
        parameter.name == "key" or parameter.kind is Parameter.VAR_KEYWORD
        for parameter in parameters
    )
    if supports_key:
        chart(data, key=key)
        return

    frame = data.to_frame() if isinstance(data, pd.Series) else data.copy()
    index_name = frame.index.name or "index"
    frame.index.name = index_name
    value_columns = [str(column) for column in frame.columns]
    long_frame = frame.reset_index().melt(
        id_vars=[index_name],
        value_vars=value_columns,
        var_name="series",
        value_name="value",
    )
    st.vega_lite_chart(
        long_frame,
        {
            "mark": "bar" if chart_name == "bar_chart" else "line",
            "encoding": {
                "x": {"field": index_name, "type": "temporal"},
                "y": {"field": "value", "type": "quantitative"},
                "color": {"field": "series", "type": "nominal"},
            },
        },
        width="stretch",
        key=key,
    )


def _equity_figure(go: Any, equity_curve: pd.DataFrame) -> Any:
    figure = go.Figure()
    if not equity_curve.empty and {"timestamp", "equity"}.issubset(equity_curve.columns):
        figure.add_trace(
            go.Scatter(
                x=equity_curve["timestamp"],
                y=equity_curve["equity"],
                mode="lines",
                name="Equity",
                line={"color": "#2563eb", "width": 2},
            )
        )
    figure.update_layout(title="Equity Curve", height=320, margin={"l": 8, "r": 8, "t": 48, "b": 8})
    return figure


def _daily_pnl_figure(go: Any, trades: pd.DataFrame) -> Any:
    figure = go.Figure()
    if not trades.empty and {"exit_time", "net_pnl"}.issubset(trades.columns):
        working = trades.copy()
        working["date"] = pd.to_datetime(working["exit_time"]).dt.date
        daily = working.groupby("date", as_index=False)["net_pnl"].sum()
        figure.add_trace(
            go.Bar(
                x=daily["date"],
                y=daily["net_pnl"],
                name="Daily PnL",
                marker={"color": "#0f766e"},
            )
        )
    figure.update_layout(title="Daily PnL", height=280, margin={"l": 8, "r": 8, "t": 48, "b": 8})
    return figure


def _kline_figure(go: Any, bars: pd.DataFrame, trades: pd.DataFrame) -> Any:
    figure = go.Figure()
    figure.add_trace(
        go.Candlestick(
            x=bars["timestamp"],
            open=bars["open"],
            high=bars["high"],
            low=bars["low"],
            close=bars["close"],
            name="TMF",
        )
    )
    for column, color in (("vwap", "#7c3aed"), ("ema_fast", "#ea580c"), ("ema_slow", "#475569")):
        if column in bars.columns:
            figure.add_trace(
                go.Scatter(
                    x=bars["timestamp"],
                    y=bars[column],
                    mode="lines",
                    name=column,
                    line={"color": color, "width": 1.5},
                )
            )
    if not trades.empty:
        _add_trade_markers(go, figure, trades, "entry_time", "entry_price", "Entry", "#16a34a")
        _add_trade_markers(go, figure, trades, "exit_time", "exit_price", "Exit", "#dc2626")
    figure.update_layout(
        title="K-line with VWAP, EMA, Entries, and Exits",
        height=520,
        xaxis_rangeslider_visible=False,
        margin={"l": 8, "r": 8, "t": 48, "b": 8},
    )
    return figure


def _add_trade_markers(
    go: Any,
    figure: Any,
    trades: pd.DataFrame,
    time_column: str,
    price_column: str,
    name: str,
    color: str,
) -> None:
    if {time_column, price_column}.issubset(trades.columns):
        figure.add_trace(
            go.Scatter(
                x=trades[time_column],
                y=trades[price_column],
                mode="markers",
                name=name,
                marker={"color": color, "size": 9, "symbol": "diamond"},
            )
        )


def _summarize_parquet_files(files: list[Path]) -> DataSummary:
    if not files:
        return DataSummary(0, 0, "-", "-", "-")

    frames = [read_parquet(path) for path in files]
    data = pd.concat(frames, ignore_index=True)
    if data.empty:
        return DataSummary(len(files), 0, "-", "-", "-")

    timestamps = pd.to_datetime(data["timestamp"]) if "timestamp" in data.columns else pd.Series()
    start = str(timestamps.min()) if not timestamps.empty else "-"
    end = str(timestamps.max()) if not timestamps.empty else "-"
    contracts = "-"
    if "contract" in data.columns:
        contracts = ", ".join(sorted(set(data["contract"].astype(str)))) or "-"
    return DataSummary(len(files), len(data), start, end, contracts)


def _load_bar_preview(processed_dir: Path, symbol: str, timeframe: str) -> pd.DataFrame:
    bar_dir = processed_dir / "bars" / symbol / timeframe
    if not bar_dir.exists() or not bar_dir.is_dir():
        return pd.DataFrame()
    files = sorted(
        path for path in bar_dir.iterdir() if path.is_file() and path.suffix == ".parquet"
    )
    if not files:
        return pd.DataFrame()
    return pd.concat([read_parquet(path) for path in files[-3:]], ignore_index=True)


def _load_chart_bars(config: BacktestConfig) -> pd.DataFrame:
    try:
        bars = load_configured_bars(config)
        selected = select_contract_bars(
            bars,
            contract_mode=config.data.contract_mode,
            contract=config.data.contract,
            roll_confirmation_days=config.data.roll_confirmation_days,
        ).bars
        params: dict[str, object] = dict(config.strategy.params)
        enriched = append_basic_indicators(
            selected,
            ema_fast=_int_param(params, "ema_fast", 20),
            ema_slow=_int_param(params, "ema_slow", 60),
            atr_period=_int_param(params, "atr_period", 14),
            volatility_window=_int_param(params, "volatility_window", 20),
        )
        return enriched.tail(500).reset_index(drop=True)
    except (OSError, ValueError):
        return pd.DataFrame()


def _date_range_label(summary: DataSummary) -> str:
    if summary.start == "-" or summary.end == "-":
        return "-"
    return f"{summary.start[:10]} to {summary.end[:10]}"


def _money(value: object) -> str:
    return f"{_coerce_float(value):,.2f}"


def _pct(value: object) -> str:
    return f"{_coerce_float(value):.2%}"


def _coerce_float(value: object) -> float:
    if isinstance(value, str | int | float):
        return float(value)
    return 0.0


def _int_param(params: dict[str, object], name: str, default: int) -> int:
    value = params.get(name, default)
    if isinstance(value, str | int):
        return int(value)
    return default


def _float_param(params: dict[str, object], name: str, default: float) -> float:
    value = params.get(name, default)
    if isinstance(value, str | int | float):
        return float(value)
    return default


def _time_param(params: dict[str, object], name: str, default: time) -> time:
    value = params.get(name)
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        try:
            return time.fromisoformat(value)
        except ValueError:
            return default
    return default


def _inject_style(st: Any) -> None:
    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.2rem; padding-bottom: 2rem; }
        h1 { font-size: 1.75rem; letter-spacing: 0; }
        h2, h3 { letter-spacing: 0; }
        [data-testid="stMetricValue"] { font-size: 1.2rem; }
        [data-testid="stSidebar"] { min-width: 20rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
