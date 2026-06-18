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
- cost, slippage, metrics, and report output
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
Current next task: Task 3 — Data Schemas and Storage.

The `backtest` command currently validates `configs/v1_backtest.yaml` and then stops before execution. Data import, bar generation, strategy logic, backtesting execution, and Streamlit UI are implemented in later tasks.

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
