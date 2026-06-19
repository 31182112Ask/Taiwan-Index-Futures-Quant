"""Streamlit Backtest Lab for V1 local research workflows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, time
from importlib import import_module
from pathlib import Path
from typing import Any

import pandas as pd

from tifq.backtest import (
    BacktestResult,
    persist_backtest_result,
    run_backtest_from_config,
)
from tifq.backtest.engine import load_configured_bars
from tifq.backtest.metrics import MetricValue
from tifq.bars import build_bar_files, discover_tick_files
from tifq.config import ConfigLoadError, load_backtest_config
from tifq.config.models import BacktestConfig
from tifq.data import import_taifex_ticks
from tifq.data.storage import read_parquet
from tifq.indicators import append_basic_indicators


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
        for path in raw_path.iterdir()
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
) -> tuple[dict[str, MetricValue], pd.DataFrame, pd.DataFrame]:
    """Load metrics, trades, and equity curve from a persisted result run."""
    path = Path(run_dir)
    metrics = json.loads((path / "metrics.json").read_text(encoding="utf-8"))
    trades = pd.read_csv(path / "trades.csv")
    equity_curve = pd.read_csv(path / "equity_curve.csv")
    return metrics, trades, equity_curve


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
        "Initial cash",
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
    )


def _render_data_import(st: Any, config: BacktestConfig) -> None:
    st.subheader("Data Import")
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
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No local TAIFEX CSV or ZIP files found in the selected raw directory.")

    if st.button("Import TAIFEX", type="primary"):
        try:
            summary = import_taifex_ticks(config.data.raw_dir, config.data.processed_dir)
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
                }
            )


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

    if st.button("Build bars", type="primary"):
        try:
            build_summary = build_bar_files(
                config.data.processed_dir,
                symbol=config.data.symbol,
                timeframe=config.data.timeframe,
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
                }
            )

    preview = _load_bar_preview(
        config.data.processed_dir,
        config.data.symbol,
        config.data.timeframe,
    )
    if not preview.empty:
        st.dataframe(preview.tail(200), use_container_width=True, hide_index=True)


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


def _render_run_backtest(st: Any, go: Any, config: BacktestConfig) -> None:
    st.subheader("Run Backtest")
    if st.button("Run backtest", type="primary"):
        try:
            result = run_backtest_from_config(config)
            paths = persist_backtest_result(config, result)
        except (OSError, ValueError) as exc:
            st.error(f"Backtest failed: {exc}")
        else:
            st.session_state["last_result"] = result
            st.session_state["last_run_dir"] = str(paths.run_dir)
            st.success(f"Backtest saved to {paths.run_dir}")

    result = st.session_state.get("last_result")
    if isinstance(result, BacktestResult):
        _render_result_summary(st, result.metrics)
        chart_bars = _load_chart_bars(config)
        _render_charts(st, go, result.equity_curve, result.trades, chart_bars)
        st.dataframe(result.trades, use_container_width=True, hide_index=True)
        st.caption(f"Latest run directory: {st.session_state.get('last_run_dir', '-')}")
    else:
        st.info("Run a backtest to view equity, daily PnL, K-line overlays, and trades.")


def _render_result_browser(st: Any, go: Any, config: BacktestConfig) -> None:
    st.subheader("Result Browser")
    runs = discover_result_runs(config.data.processed_dir.parent / "results" / "backtests")
    if not runs:
        st.info("No persisted backtest runs found.")
        return

    labels = [f"{run.strategy} / {run.run_id}" for run in runs]
    selected_label = st.selectbox("Run", labels)
    selected = runs[labels.index(selected_label)]
    metrics, trades, equity_curve = load_result_run(selected.run_dir)
    _render_result_summary(st, metrics)
    _render_charts(st, go, equity_curve, trades, pd.DataFrame())
    st.dataframe(trades, use_container_width=True, hide_index=True)
    st.caption(str(selected.run_dir))


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


def _render_charts(
    st: Any,
    go: Any | None,
    equity_curve: pd.DataFrame,
    trades: pd.DataFrame,
    chart_bars: pd.DataFrame,
) -> None:
    if go is not None:
        st.plotly_chart(_equity_figure(go, equity_curve), use_container_width=True)
        st.plotly_chart(_daily_pnl_figure(go, trades), use_container_width=True)
        if not chart_bars.empty:
            st.plotly_chart(_kline_figure(go, chart_bars, trades), use_container_width=True)
        return

    st.warning("Plotly is not installed in this environment; using Streamlit native charts.")
    if not equity_curve.empty and {"timestamp", "equity"}.issubset(equity_curve.columns):
        st.write("Equity Curve")
        st.line_chart(equity_curve.set_index("timestamp")["equity"])
    if not trades.empty and {"exit_time", "net_pnl"}.issubset(trades.columns):
        working = trades.copy()
        working["date"] = pd.to_datetime(working["exit_time"]).dt.date
        daily = working.groupby("date")["net_pnl"].sum()
        st.write("Daily PnL")
        st.bar_chart(daily)
    if not chart_bars.empty:
        columns = [
            column
            for column in ("close", "vwap", "ema_fast", "ema_slow")
            if column in chart_bars.columns
        ]
        if columns:
            st.write("Price and Indicators")
            st.line_chart(chart_bars.set_index("timestamp")[columns])


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
        params: dict[str, object] = dict(config.strategy.params)
        return append_basic_indicators(
            bars.tail(500),
            ema_fast=_int_param(params, "ema_fast", 20),
            ema_slow=_int_param(params, "ema_slow", 60),
            atr_period=_int_param(params, "atr_period", 14),
            volatility_window=_int_param(params, "volatility_window", 20),
        )
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
