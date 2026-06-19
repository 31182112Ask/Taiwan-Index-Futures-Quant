# AGENTS.md

This file defines the development instructions for AI coding agents working on this repository.

Repository: `Taiwan-Index-Futures-Quant`
Project goal: build a local quantitative research, backtesting, and eventually automated trading system for Taiwan index futures, starting with Micro TAIEX Futures / 微型台指期貨 / TMF.

## 0. Core Principle

This project must be developed in clearly separated stages.

Do not skip stages.
Do not implement live trading before the research, backtesting, replay, paper trading, and simulation layers are reliable.
Do not add broker API integration in V1.
Do not add real-money order placement in V1.

Before moving from one version to the next, the current version must be optimized, tested, reviewed, and refined until the project owner is satisfied.

The project owner will decide when the next version begins.

Current active version: **V1 — Backtest Lab**.

---

# 1. Version Roadmap

## V0 — Project Bootstrap

Purpose:

Set up the Python project structure, development environment, package layout, configuration system, testing framework, and basic CLI.

No trading logic.
No broker API.
No live market data.
No real orders.

## V1 — Backtest Lab

Purpose:

Build a local historical backtesting platform using real historical data.

V1 must support:

* Local historical data import
* Tick cleaning
* Tick to K-line conversion
* Indicator calculation
* Strategy signal generation
* Conservative backtesting
* Cost model
* Performance report
* Streamlit-based local client
* CLI commands

V1 must not support:

* Real-time market feed
* Broker API integration
* Paper trading
* Simulation broker
* Real-money trading
* Automated order placement
* Account or margin synchronization
* Live position management

V1 may support explicit, low-frequency synchronization of recent historical CSV files
from public official TAIFEX download links. This synchronization must remain
idempotent, user-triggered, and limited to local historical research data. It must
not bypass access controls, authentication, CAPTCHA, rate limits, or anti-bot
mechanisms, and it must not introduce broker integration, live-market feeds, or
order placement.

## V2 — Replay Engine

Purpose:

Replay historical tick or bar data in chronological order to simulate real-time market data flow.

This version tests whether the system can behave correctly under event-driven conditions.

No real-time feed.
No real orders.

## V3 — Paper Live

Purpose:

Connect to real-time market data and generate real-time K-lines, indicators, and strategy signals.

No real orders.
No broker order API.
The system only records signals and simulated decisions.

## V4 — Broker Simulation

Purpose:

Connect to broker simulation mode and test order placement, order status updates, fills, position tracking, and account-like state handling.

Only simulated orders are allowed.

## V5 — Manual Confirm Live

Purpose:

Allow real broker connection, but every real order must require manual confirmation from the project owner.

The system may generate signals and order proposals, but it must not place live orders without explicit human confirmation.

## V6 — Full Auto Live

Purpose:

Allow limited real-money automated trading under strict permission controls.

Initial limits must be conservative:

* TMF only
* Day session only
* Maximum 1 contract
* No overnight holding
* Strong kill switch
* Hard daily loss limit
* Broker position reconciliation
* Manual unlock required before enabling full auto mode

---

# 2. Current Development Target: V1 Backtest Lab

V1 is the current active version.

The objective of V1 is to build a reliable local research and historical backtesting platform for TMF.

V1 should answer this question:

Can a strategy be tested reproducibly on historical TMF data with realistic assumptions about execution, cost, slippage, and risk?

V1 is not expected to produce a profitable strategy immediately.
The priority is correctness, reproducibility, modularity, and extensibility.

---

# 3. V1 Scope

## 3.1 Supported Product

V1 supports only:

* Product: TMF / Micro TAIEX Futures / 微型台指期貨
* Point value: NTD 10 per index point
* Tick size: 1 point
* Trading session: day session only
* Position size: -1, 0, +1 in the initial implementation

Do not add TX, MTX, options, stocks, ETFs, or multi-product logic in V1 unless explicitly requested later.

## 3.2 Supported Session

V1 supports only day session.

Default assumptions:

* Day session start: 08:45:00 Asia/Taipei
* Day session end: 13:45:00 Asia/Taipei
* Force flatten time: 13:35:00 Asia/Taipei

Do not implement night session in V1.

Night session crosses calendar dates and introduces additional complexity. It belongs to a later version.

## 3.3 Supported Data Flow

V1 data flow:

```text
Raw TAIFEX historical data
        ↓
Local import and normalization
        ↓
Clean tick data
        ↓
Parquet storage
        ↓
1m / 5m OHLCV bar generation
        ↓
Indicator calculation
        ↓
Strategy signal generation
        ↓
Backtest execution
        ↓
Trades, equity curve, metrics, report
```

## 3.4 Data Policy

Do not commit raw market data to the repository.

The following directories must be ignored by git except `.gitkeep` files:

```text
data/raw/
data/processed/
data/results/
logs/
```

Do not commit API keys, account credentials, broker certificates, or private configuration files.

---

# 4. Required V1 Project Structure

Use a Python `src` layout.

Recommended structure:

```text
Taiwan-Index-Futures-Quant/
  README.md
  AGENTS.md
  pyproject.toml
  .gitignore
  .env.example
  ruff.toml
  mypy.ini
  pytest.ini

  configs/
    v1_backtest.yaml
    strategies/
      vwap_trend.yaml
      opening_range.yaml

  data/
    raw/
      taifex/
        .gitkeep
    processed/
      ticks/
        .gitkeep
      bars/
        .gitkeep
    results/
      backtests/
        .gitkeep

  docs/
    architecture/
      version_plan.md
      v1_backtest_lab.md
    data/
      taifex_import_format.md
    strategy/
      vwap_trend.md

  src/
    tifq/
      __init__.py
      cli.py

      config/
        __init__.py
        models.py
        loader.py

      contracts/
        __init__.py
        product.py
        calendar.py

      data/
        __init__.py
        schemas.py
        taifex_loader.py
        tick_cleaner.py
        storage.py

      bars/
        __init__.py
        builder.py
        resampler.py

      indicators/
        __init__.py
        ema.py
        atr.py
        vwap.py
        volatility.py

      strategy/
        __init__.py
        base.py
        signals.py
        vwap_trend.py
        opening_range.py

      backtest/
        __init__.py
        engine.py
        broker_sim.py
        cost.py
        portfolio.py
        metrics.py
        report.py

      apps/
        __init__.py
        backtest_lab.py

      utils/
        __init__.py
        logging.py
        time.py
        io.py

  tests/
    unit/
      test_bar_builder.py
      test_indicators.py
      test_cost.py
      test_portfolio.py
      test_vwap_trend.py
    integration/
      test_backtest_engine.py
```

Package name: `tifq`.

---

# 5. Python Environment

Use Python 3.11 or newer.

Recommended dependencies for V1:

```text
pandas
numpy
polars
pyarrow
duckdb
pydantic
pyyaml
typer
streamlit
plotly
rich
pytest
ruff
mypy
```

Do not add Shioaji or any broker API dependency in V1.

Broker-related dependencies belong to V3 or later.

---

# 6. Configuration System

V1 must use YAML configuration and Pydantic validation.

Main config file:

```text
configs/v1_backtest.yaml
```

Recommended baseline:

```yaml
project:
  name: Taiwan Index Futures Quant
  timezone: Asia/Taipei

data:
  symbol: TMF
  contract_mode: continuous_front_month
  raw_dir: data/raw/taifex
  processed_dir: data/processed
  start_date: "2026-05-18"
  end_date: "2026-06-17"
  session: day
  timeframe: 5m

product:
  point_value: 10
  tick_size: 1
  exchange: TAIFEX

cost:
  commission_per_side: 5
  tax_rate: 0.00002
  slippage_points_per_side: 1

strategy:
  name: vwap_trend
  params:
    ema_fast: 20
    ema_slow: 60
    atr_period: 14
    atr_stop_mult: 1.5
    take_profit_r: 1.5
    min_atr_points: 10
    max_atr_points: 120
    max_trades_per_day: 3
    force_flatten_time: "13:35:00"
    no_entry_before: "08:55:00"
    no_entry_after: "13:20:00"

portfolio:
  initial_cash: 100000
  max_position: 1
  allow_short: true
```

Validation requirements:

* `symbol` must be `TMF` in V1.
* `timeframe` must be `1m` or `5m`.
* `start_date <= end_date`.
* `point_value` defaults to `10`.
* `max_position >= 0`.
* `commission_per_side >= 0`.
* `tax_rate >= 0`.
* `slippage_points_per_side >= 0`.

---

# 7. Data Models

V1 must define clear internal schemas.

## 7.1 Tick

Required fields:

```text
symbol
contract
timestamp
price
volume
source
```

## 7.2 Bar

Required fields:

```text
symbol
contract
timeframe
timestamp
open
high
low
close
volume
```

## 7.3 Signal

Required fields:

```text
timestamp
symbol
side
target_position
reason
stop_loss
take_profit
```

Signal side values:

```text
BUY
SELL
FLAT
HOLD
```

## 7.4 Trade

Required fields:

```text
entry_time
exit_time
symbol
side
qty
entry_price
exit_price
gross_pnl
fee
tax
slippage
net_pnl
exit_reason
```

---

# 8. Data Import

V1 should support manual import of TAIFEX historical CSV or ZIP files.

Input directory:

```text
data/raw/taifex/
```

Output directory:

```text
data/processed/ticks/
```

Importer requirements:

* Read `.csv` files.
* Read `.zip` files containing `.csv` files.
* Normalize columns into the internal Tick schema.
* Parse timestamps as `Asia/Taipei`.
* Filter product to `TMF`.
* Drop rows with invalid price.
* Drop rows with invalid volume.
* Sort ticks ascending by timestamp.
* Save cleaned ticks as Parquet.

Do not assume one fixed column language forever.

If TAIFEX column names vary, implement a column mapping layer instead of hard-coding one fragile format.

---

# 9. Bar Builder

V1 must generate OHLCV bars from cleaned ticks.

Supported timeframes:

```text
1m
5m
```

Rules:

* Use `Asia/Taipei` timezone.
* Group by `symbol`, `contract`, and trading date.
* Resample price into OHLC.
* Sum volume.
* Drop bars with no trades.
* Do not forward-fill empty bars.
* Do not aggregate across trading days.
* Sort output by timestamp ascending.

Output path convention:

```text
data/processed/bars/TMF/1m/YYYY-MM-DD.parquet
data/processed/bars/TMF/5m/YYYY-MM-DD.parquet
```

---

# 10. Indicators

V1 must implement:

* EMA
* VWAP
* ATR
* Realized volatility

## 10.1 EMA

EMA must use only current and past data.

Do not use future data.

## 10.2 VWAP

VWAP must reset each trading day.

Do not calculate VWAP cumulatively across multiple trading days.

## 10.3 ATR

ATR must use current and past bars only.

Do not use future data.

V1 may use a simple rolling average ATR. Wilder ATR may be added later.

---

# 11. V1 Strategy: VWAP Trend

The first strategy to implement is:

```text
VWAPTrendStrategy
```

The strategy must only emit signals.

The strategy must not:

* Place orders
* Modify portfolio state directly
* Read broker state
* Access live accounts
* Perform execution logic

## 11.1 Long Entry

Long entry is allowed only when:

```text
current_position == 0
time >= no_entry_before
time <= no_entry_after
ATR is between min_atr_points and max_atr_points
close > VWAP
EMA_FAST > EMA_SLOW
previous_close <= previous_EMA_FAST
close > EMA_FAST
```

## 11.2 Short Entry

Short entry is allowed only when:

```text
current_position == 0
time >= no_entry_before
time <= no_entry_after
ATR is between min_atr_points and max_atr_points
close < VWAP
EMA_FAST < EMA_SLOW
previous_close >= previous_EMA_FAST
close < EMA_FAST
```

## 11.3 Exit Rules

Long position:

```text
stop_loss = entry_price - ATR * atr_stop_mult
take_profit = entry_price + (entry_price - stop_loss) * take_profit_r
```

Short position:

```text
stop_loss = entry_price + ATR * atr_stop_mult
take_profit = entry_price - (stop_loss - entry_price) * take_profit_r
```

Additional exit rules:

```text
force flatten at 13:35
exit on reverse signal
do not exceed max_trades_per_day
no overnight position
```

No scaling in.
No averaging down.
No martingale.
No grid strategy.
No overnight holding in V1.

---

# 12. Backtest Execution Model

V1 must use conservative execution assumptions.

Default rule:

```text
Signal is generated after bar N closes.
Execution occurs at bar N+1 open.
```

This prevents look-ahead bias.

## 12.1 Cost Model

Each trade must include:

* Commission
* Transaction tax
* Slippage

Transaction tax formula:

```text
tax = price * point_value * tax_rate * qty
```

Tax applies on both entry and exit.

Commission applies on both entry and exit.

Slippage must be applied conservatively:

```text
buy price = raw price + slippage_points_per_side
sell price = raw price - slippage_points_per_side
```

## 12.2 PnL

Long gross PnL:

```text
(exit_price - entry_price) * point_value * qty
```

Short gross PnL:

```text
(entry_price - exit_price) * point_value * qty
```

Net PnL:

```text
gross_pnl - fee - tax - slippage_cost
```

---

# 13. Backtest Metrics

V1 must output at least:

```text
initial_cash
final_equity
net_pnl
return_pct
max_drawdown
max_drawdown_pct
trade_count
win_rate
avg_win
avg_loss
profit_factor
expectancy
largest_win
largest_loss
total_fee
total_tax
total_slippage
```

Sharpe and Sortino may be included, but V1 reports must state that one-month results have limited statistical reliability.

---

# 14. Output Files

Each backtest run should save:

```text
data/results/backtests/<strategy_name>/<run_id>/
  config.yaml
  trades.csv
  equity_curve.csv
  metrics.json
  report.html
```

Every result must be reproducible from its saved `config.yaml`.

---

# 15. CLI Requirements

V1 must provide a Typer CLI entry point:

```text
tifq = tifq.cli:app
```

Required commands:

```bash
tifq init
tifq import-taifex --raw-dir data/raw/taifex --symbol TMF
tifq build-bars --symbol TMF --timeframe 1m
tifq build-bars --symbol TMF --timeframe 5m
tifq backtest --config configs/v1_backtest.yaml
tifq app backtest-lab
```

CLI behavior:

## `tifq init`

* Create local data directories if missing.
* Create `.gitkeep` files if missing.
* Check whether config files exist.
* Print project status.

## `tifq import-taifex`

* Import raw TAIFEX files.
* Normalize to internal tick schema.
* Save cleaned tick Parquet files.

## `tifq build-bars`

* Load cleaned ticks.
* Generate 1m or 5m bars.
* Save bar Parquet files.

## `tifq backtest`

* Load YAML config.
* Load bars.
* Calculate indicators.
* Run strategy.
* Run conservative backtest.
* Save result files.

## `tifq app backtest-lab`

* Start the Streamlit Backtest Lab client.

---

# 16. Streamlit Client: Backtest Lab

V1 must include a local Streamlit client.

File:

```text
src/tifq/apps/backtest_lab.py
```

The Streamlit app should include these sections:

## 16.1 Data Import

Features:

* Select raw data directory.
* List discovered CSV/ZIP files.
* Import and clean data.
* Show tick count.
* Show date range.
* Show symbol and contract summary.
* Show invalid row count.

## 16.2 Bar Builder

Features:

* Select symbol.
* Select timeframe: `1m` or `5m`.
* Select date range.
* Generate bars.
* Preview OHLCV.
* Show missing or sparse bar statistics.

## 16.3 Strategy Config

Features:

* Select strategy: `VWAP Trend`.
* Set `ema_fast`.
* Set `ema_slow`.
* Set `atr_period`.
* Set `atr_stop_mult`.
* Set `take_profit_r`.
* Set commission.
* Set tax rate.
* Set slippage.
* Set initial cash.
* Set max position.

## 16.4 Run Backtest

Show:

* Final equity
* Net PnL
* Return percentage
* Max drawdown
* Win rate
* Trade count
* Profit factor
* Total commission
* Total tax
* Total slippage

Charts:

* Equity curve
* Daily PnL
* K-line chart with VWAP, EMA, entry, and exit marks
* Trade table

## 16.5 Result Browser

Features:

* Browse previous backtest runs.
* Load `metrics.json`.
* Load `trades.csv`.
* Compare parameter runs.

---

# 17. Testing Requirements

Every major module must have tests.

Minimum test files:

```text
tests/unit/test_bar_builder.py
tests/unit/test_indicators.py
tests/unit/test_cost.py
tests/unit/test_portfolio.py
tests/unit/test_vwap_trend.py
tests/integration/test_backtest_engine.py
```

## 17.1 Bar Builder Tests

Must verify:

* Tick data aggregates correctly into 1m bars.
* OHLC values are correct.
* Volume is correct.
* Empty minutes are skipped.
* Different trading days are not mixed.

## 17.2 Indicator Tests

Must verify:

* EMA output length matches input length.
* VWAP resets every trading day.
* ATR does not use future data.

## 17.3 Cost Tests

Must verify:

* Buy price includes positive slippage.
* Sell price includes negative slippage.
* Tax uses `price * point_value * tax_rate`.
* Commission is applied per side.

## 17.4 Portfolio Tests

Must verify:

* Long PnL is correct.
* Short PnL is correct.
* Position becomes zero after exit.
* Trade record is complete.

## 17.5 Backtest Engine Tests

Must verify:

* Signal on bar N executes on bar N+1 open.
* Force flatten works.
* Max trades per day works.
* Fees, taxes, and slippage are included.
* Metrics, trades, and equity curve are generated.

---

# 18. Code Quality Rules

General rules:

* Keep modules small and explicit.
* Use type hints.
* Avoid hidden global state.
* Do not put strategy logic inside the Streamlit app.
* Do not put execution logic inside strategy classes.
* Do not mix data import, strategy, and backtest execution in one file.
* Prefer pure functions for indicators.
* Prefer deterministic tests.
* Do not rely on network access in tests.
* Do not require private data to run tests.

Formatting and checks:

```bash
ruff check .
mypy src
pytest
```

The project should pass tests before moving to the next phase.

---

# 19. Forbidden in V1

The following are explicitly forbidden in V1:

```text
Broker API integration
Shioaji integration
Real-time quote subscription
Real order placement
Simulated broker order placement
Account login
Margin query from broker
Live position sync
WebSocket live dashboard
Night session trading
Multi-product trading
Machine learning models
Deep learning models
Auto-optimization that selects parameters without review
Martingale
Grid trading
Averaging down
Overnight holding
```

These may be considered in later versions only after approval.

---

# 20. V1 Acceptance Criteria

V1 is complete only when all of the following are true:

```text
1. The repository installs successfully.
2. The `tifq` CLI runs.
3. `tifq --help` works.
4. TMF historical data can be imported locally.
5. Cleaned tick data can be saved as Parquet.
6. 1m and 5m bars can be generated.
7. EMA, VWAP, and ATR are calculated correctly.
8. VWAP Trend backtest can run from config.
9. Backtest uses next-bar-open execution.
10. Backtest includes commission, tax, and slippage.
11. Backtest outputs trades.csv.
12. Backtest outputs equity_curve.csv.
13. Backtest outputs metrics.json.
14. Streamlit Backtest Lab can run locally.
15. User can modify strategy and cost parameters in the client.
16. Unit and integration tests pass.
17. Raw data and result files are not committed.
18. README explains how to run V1.
19. V1 behavior is reproducible.
20. The project owner confirms V1 is satisfactory before V2 begins.
```

---

# 21. Recommended Implementation Order for Coding Agents

Do not implement everything at once.

Follow this order:

## Task 1 — Bootstrap

Create project structure, package layout, config files, `.gitignore`, CLI placeholder, and README.

Goal:

```text
tifq --help
```

must work.

## Task 2 — Config System

Implement Pydantic config models and YAML loader.

Goal:

```text
configs/v1_backtest.yaml
```

can be loaded and validated.

## Task 3 — Data Schemas and Storage

Implement internal schemas and Parquet read/write utilities.

Goal:

clean tick and bar data can be saved and loaded.

## Task 4 — TAIFEX Importer

Implement CSV/ZIP importer and tick cleaner.

Goal:

raw historical data can be normalized into internal tick format.

## Task 5 — Bar Builder

Implement tick-to-OHLCV conversion.

Goal:

1m and 5m bars are generated correctly.

## Task 6 — Indicators

Implement EMA, VWAP, ATR, and volatility utilities.

Goal:

indicators can be appended to bar data without look-ahead bias.

## Task 7 — Strategy Interface and VWAP Trend

Implement base strategy interface and VWAPTrendStrategy.

Goal:

strategy emits signals only.

## Task 8 — Backtest Engine

Implement conservative historical backtest engine.

Goal:

signals become simulated trades with realistic cost assumptions.

## Task 9 — Metrics and Reports

Implement metrics calculation and result output.

Goal:

each run produces `config.yaml`, `trades.csv`, `equity_curve.csv`, and `metrics.json`.

## Task 10 — Streamlit Backtest Lab

Implement the local parameter-testing client.

Goal:

the user can run and compare backtests from a browser UI.

---

# 22. Guidance for Future Versions

This `AGENTS.md` will be updated phase by phase.

When V1 is complete and approved, add a new section for V2.

Do not remove V1 instructions unless they are obsolete and replaced by more precise architecture.

Before starting V2, perform a V1 review:

```text
Code quality review
Test coverage review
Backtest correctness review
Data import robustness review
Client usability review
Performance bottleneck review
Project owner satisfaction review
```

Only after that review should V2 begin.

The same rule applies to all future versions.

Every version must be optimized until the project owner is satisfied before moving forward.

---

# 23. Financial and Safety Disclaimer

This project is for quantitative research and software development.

It is not financial advice.

Historical backtest performance does not guarantee future results.

Automated trading can cause real financial losses.

No live trading functionality should be added until the project owner explicitly authorizes the relevant version and all previous stages are complete.

---

# 24. Future Replay and Professional Chart UI Requirements

The project must eventually support a replayable K-line testing interface and a professional chart-like interface.

Future interface requirements:

* Monthly, weekly, daily, and intraday candlestick views.
* Play, pause, fast-forward, slow-down, and step-forward historical replay.
* Model indicator lines.
* Forecast or expected-operation overlays.
* Entry, exit, stop-loss, take-profit, and force-flatten markers.
* Strategy state and reason display per bar.
* Future live-mode compatibility through a shared event stream.

Implementation rules:

* Strategies must not draw directly on charts.
* Indicators must not draw directly on charts.
* Strategy and indicator modules should emit structured data only.
* UI layers are responsible for rendering chart overlays.
* Future chart rendering should consume structured bar, indicator, signal, model, and trade events.
* A replay/chart event stream should be introduced before the final Backtest Lab UI.
* Task 6 should only prepare indicator data; it must not implement replay or UI.

---

# 25. Streamlit Element Identity Rules

Streamlit elements that may be rendered more than once in one script run must use explicit,
deterministic keys. This includes equivalent charts or tables rendered in separate tabs.

Rules:

* Treat every tab body as part of the same Streamlit script run, including tabs that are not selected.
* Dynamic strategy names and result run IDs must pass through a key sanitizer before use.
* Sanitized keys may contain only `A-Z`, `a-z`, `0-9`, `_`, and `-`.
* Do not use UUIDs, random values, timestamps, or other values that change between reruns as keys.
* Run Backtest and Result Browser elements must have distinct key prefixes even when they display
  identical result data.
* UI changes must be exercised in a real browser, checked for terminal exceptions, and verified with
  screenshots before completion.

---

# 26. V1 Data Lifecycle And Runtime Invariants

The following rules are mandatory for all V1 maintenance:

* Never mix multiple contracts directly by timestamp into one strategy sequence. Contract selection
  must produce at most one active contract per timestamp and one active contract per trading day.
* EMA, ATR, realized volatility, and strategy previous-row logic must not cross a
  `contract_segment_id` boundary.
* Startup cleanup may delete only explicitly allowlisted disposable artifacts. Raw CSV/ZIP,
  processed Parquet, manifests, and result runs must never be automatically permanently deleted.
* Duplicate or conflicting valuable files require a dry-run plan and explicit confirmation; the
  default confirmed action is quarantine, not deletion.
* Core functions expose progress through optional callbacks and must not import Streamlit.
* An unchanged import or bar build must be a true no-op and must not rewrite output Parquet.
* UI caches must be invalidated by path, size, and nanosecond-mtime fingerprints.
* Result Browser charts and diagnostics must use persisted run artifacts, not recomputed current
  processed data.
* Long operations must expose phase, completed/total when known, elapsed time, and factual ETA when
  enough samples exist. Do not add artificial delays.
* UI changes require real browser interaction, screenshots, and terminal-log inspection.

---

# 27. Task 12 Correctness And Workflow Invariants

* A signal and its same-row model bar must align on timestamp, symbol, contract, and
  `contract_segment_id` before execution begins.
* A signal from an old contract segment never executes on a new segment. Any open position closes
  on the old segment's final available bar with `contract_roll` before new-contract valuation.
* Continuous-contract volume decisions use only the previous completed trading day and become
  effective on the next trading day. Current-day full volume never changes current-day selection.
* All data writers acquire `data_pipeline.lock` before an operation-specific lock. No operation may
  reverse this order.
* Import and bar build stage complete candidate outputs and manifest before publish. Outputs publish
  first, manifest publishes last, and any failure restores the prior formal state.
* Incremental no-op checks validate output SHA-256 in addition to source fingerprint and code
  version. Existing files with mismatched hashes are corrupt inputs to rebuild, not cache hits.
* Backtest preflight is explicit and produces model bars, aligned signals, diagnostics, and a data
  fingerprint without executing trades. Execution rejects stale preflight artifacts.
* The eight primary V1 operations remain in one top-level workflow row. Completion and warning
  markers must be computed from current manifests, hashes, diagnostics, and persisted artifacts.
