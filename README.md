# Taiwan Index Futures Quant

Local quantitative research and historical backtesting project for Taiwan Micro TAIEX Futures (TMF).

Current active stage: **V1 - Backtest Lab**.

V1 focuses on reproducible local research only:

- historical TAIFEX data import
- tick cleaning
- 1m / 5m bar generation
- indicator calculation
- strategy signal generation
- conservative next-bar-open backtesting
- cost and slippage accounting
- metrics and report output
- local Streamlit Backtest Lab

V1 explicitly excludes broker APIs, live feeds, paper live trading, simulated broker order placement, and real-money trading.

## Setup

Use Python 3.11 or newer.

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e .[dev]
```

If dependencies are already installed and you only need the CLI entry point:

```bash
python -m pip install -e . --no-deps
```

## CLI

```bash
tifq --help
tifq init
tifq import-taifex --raw-dir data/raw/taifex --symbol TMF
tifq build-bars --symbol TMF --timeframe 5m
tifq backtest --config configs/v1_backtest.yaml
tifq app backtest-lab
```

Task 1 completed: project bootstrap.
Task 2 completed: config loading and validation.
Task 3 completed: data schemas and Parquet storage helpers.
Task 4 completed: TAIFEX CSV/ZIP import and tick cleaning.
Task 5 completed: tick Parquet to 1m/5m OHLCV bars.
Task 6 completed: EMA, VWAP, ATR, and realized volatility indicators.
Task 7 completed: strategy interface and VWAP Trend signals.
Task 8 completed: conservative next-bar-open backtest engine.
Task 9 completed: metrics calculation and persisted result files.
Current next task: Task 10 - Streamlit Backtest Lab.

The `import-taifex` command imports local TAIFEX CSV/ZIP files into cleaned daily tick Parquet files. The `build-bars` command converts cleaned ticks into 1m or 5m OHLCV bar files. The `backtest` command validates `configs/v1_backtest.yaml`, loads configured bar Parquet files, calculates indicators, generates VWAP Trend signals, runs conservative next-bar-open execution, and writes `config.yaml`, `trades.csv`, `equity_curve.csv`, and `metrics.json` under `data/results/backtests/<strategy_name>/<run_id>/`. The Streamlit UI is implemented in a later task.

## Project Layout

```text
configs/        YAML configuration files
data/           local raw, processed, and result data; ignored by git except .gitkeep files
docs/           architecture, data, and strategy notes
src/tifq/       Python package
tests/          unit and integration tests
```

## Safety

This project is for quantitative research and software development only. Historical backtest results do not guarantee future performance. No live trading functionality belongs in V1.
