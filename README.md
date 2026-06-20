# Taiwan Index Futures Quant

> V1 is frozen at `v1.0.0`. V2 Task 0 extracts a framework-neutral application service
> boundary without changing V1 backtest behavior. Streamlit remains the supported
> legacy/reference UI; the next approved task is a FastAPI + React vertical slice.

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
tifq sync-taifex --limit 30 --timeframe 5m
tifq sync-taifex --limit 30 --plan
tifq build-bars --symbol TMF --timeframe 5m
tifq backtest --config configs/v1_backtest.yaml
tifq doctor
tifq doctor --full
tifq clean
tifq clean --apply-safe
tifq workflow --config configs/v1_backtest.yaml
tifq workflow --config configs/v1_backtest.yaml --stop-after preflight
tifq app backtest-lab
```

## Runtime Safety

`tifq doctor` performs a fast structure, manifest, temporary-file, and operation-lock check.
`--full` additionally hashes raw CSV/ZIP files to identify duplicate content. `tifq clean` is a
dry-run by default. `--apply-safe` may remove only stale `.part`, `.tmp`, and `.temp` files. Raw
market data, processed Parquet, manifests, and backtest results are never permanently deleted by
safe cleanup. Duplicate raw files and explicitly pruned results are moved to
`data/quarantine/<timestamp>/` only after an explicit command or UI confirmation.

Long sync, import, bar build, contract selection, and backtest operations emit framework-neutral
progress updates with phase, elapsed time, and ETA when enough completed samples exist. Operation
lock files under `data/.runtime/` prevent concurrent writers and recover automatically from dead
PIDs.

## Incremental Data

Official downloads are planned before writes. Valid existing files are reused, missing files are
downloaded, and unmanaged or corrupt conflicts require review. Import state is stored in
`data/processed/import_manifest.json`; bar build state is stored in
`data/processed/bar_manifest.json`. Source metadata and SHA-256, parser/builder versions, output
paths, and output hashes make unchanged second runs true no-ops without rewriting Parquet.

## Contract Integrity

V1 supports `single_contract` and `continuous_front_month`. Single-contract mode requires an exact
`YYYYMM` contract. Continuous mode selects one monthly TMF contract per trading day, starts at the
nearest unexpired month, keeps the active contract until it disappears or the next month has higher
day-session volume for `roll_confirmation_days`, and never rolls backward. Selection uses no future
trading day. The continuous series is **unadjusted**; every roll creates a new contract segment, and
EMA, ATR, and realized volatility warm up again inside that segment.

Each run persists selected model bars, signals, contract selection audit, diagnostics, timings, and
a data fingerprint in addition to config, trades, equity, and metrics. Legacy runs remain readable
but are labeled when these reproducibility artifacts are absent.

Roll decisions use only the previous completed trading day's day-session volume. A confirmed roll
becomes effective on the following trading day. If the active contract is absent, V1 moves to the
nearest later month and records `current_contract_missing`. Open positions are closed on the final
bar of the old segment with reason `contract_roll`; old-segment signals cannot execute on the new
contract's opening bar.

## Transaction Safety

Every data writer acquires `data_pipeline.lock` before its operation-specific lock. Import and bar
build first prepare complete outputs under `data/processed/.staging/`, validate schemas and hashes,
then publish outputs with the manifest replaced last. A publish failure restores prior outputs and
manifest. Unchanged checks verify source fingerprints, parser/builder versions, output existence,
and output SHA-256; a modified output is rebuilt instead of being reported as a no-op.

The Backtest Lab exposes one `V1 Workflow` row under the title:

```text
Check environment -> Plan data -> Sync data -> Import data -> Build bars
-> Backtest preflight -> Run backtest -> View results
```

Markers are derived from runtime state: `✅` complete, `⚠` blocked/warning, and `…` running.
Preflight runs only when explicitly requested and is bound to config, bar metadata, manifest hash,
and source/output hashes. A changed config or file invalidates it before execution.

The latest download plan is indexed in `data/.runtime/workflow_state.json`. This file is never the
sole source of truth: restart recovery revalidates the plan age and raw-directory fingerprint,
download manifest and file hashes, import/bar manifests and output hashes, and result fingerprint
and readable artifacts. A partial sync is always `⚠`, blocks import, and reports failed dates,
paths, errors, successful files, and whether retry is safe.

## Session Risk Controls

Normal strategy signals execute at the next bar open. Session boundaries are engine-level risk
controls:

- If a 13:35 bar exists, an open position closes at that bar's open with `session_end`.
- If 13:35 is missing, it closes at the last available pre-13:35 close with
  `session_end_fallback`.
- No new position may open at or after 13:35, and a prior-day signal never executes on the next
  trading day.
- Every trading day ends flat. A same-day contract-segment change uses `contract_roll`; session end
  takes priority over a contract change first observed on the next trading day.

Fees, tax, and slippage apply to all forced exits.

## Windows Lock Safety

Operation locks use `psutil` and verify both PID existence and process creation time. This avoids
PID-reuse false positives and never probes with `os.kill(pid, 0)` on Windows. `AccessDenied` is
treated conservatively as active; no health check or cleanup operation terminates a process.

## Capital And Diagnostics

Initial accounting cash sets starting equity only. V1 uses fixed target positions and
`max_position` to control contract quantity. Optional `assumed_margin_per_contract` is a user
assumption, not a live or official TAIFEX margin value. When a run produces zero trades, the UI
reports actual bar, indicator, ATR, candidate, signal, and execution-rejection statistics instead
of guessing or showing only empty charts.

Troubleshooting:

- Run `tifq doctor` when manifests, lock files, or incomplete temporary files are suspected.
- Run `tifq clean` first to review a cleanup plan; use `--apply-safe` only for disposable temps.
- Use `tifq sync-taifex --plan` to inspect download state before network or disk writes.
- Stop and restart Streamlit if local cached state remains stale after external file changes.

## Backtest Lab

Start the local Streamlit client with either the CLI command above or directly:

```bash
python -m streamlit run src/tifq/apps/backtest_lab.py
```

Chart elements use deterministic, explicit keys so Run Backtest and Result Browser can render
the same persisted result during one Streamlit rerun. Dynamic result run IDs are sanitized before
they are used in element keys. If Streamlit serves stale cached state during local development,
stop the process and start the application again before repeating the workflow.

Task 13 includes a repository-native Python Playwright acceptance suite. It starts Streamlit and
Chromium against isolated deterministic data, clicks the eight workflow controls, verifies desktop
alignment, partial-sync warning behavior, zero-trade diagnostics, trading charts/tables, restart
restoration, and a true no-op second run. It never substitutes mocked Streamlit calls for browser
actions and does not require live TAIFEX access in CI.

```powershell
python -m playwright install chromium
python -m pytest -q -m "not e2e"
python -m pytest -q tests/e2e --browser chromium --tracing retain-on-failure
```

Browser screenshots, traces, and terminal reports are written under ignored `artifacts/` paths.

Task 1 completed: project bootstrap.
Task 2 completed: config loading and validation.
Task 3 completed: data schemas and Parquet storage helpers.
Task 4 completed: TAIFEX CSV/ZIP import and tick cleaning.
Task 5 completed: tick Parquet to 1m/5m OHLCV bars.
Task 6 completed: EMA, VWAP, ATR, and realized volatility indicators.
Task 7 completed: strategy interface and VWAP Trend signals.
Task 8 completed: conservative next-bar-open backtest engine.
Task 9 completed: metrics calculation and persisted result files.
Task 10 completed: local Streamlit Backtest Lab.
V1 hardening 10.1 completed: explicit official TAIFEX recent trading-day CSV sync.
V1 final hardening Task 11 completed: contract integrity, runtime safety, incremental manifests,
progress UX, and reproducible diagnostics.
Task 12 completed: final correctness, transaction safety, shared locking, and linear workflow.
Current task: Task 13 — V1 final release acceptance. V2 remains blocked until all local, browser,
official-data, and Windows/Ubuntu CI checks pass.

The `sync-taifex` command retrieves the most recent official TAIFEX futures time-and-sales CSV files advertised by the public previous-30-trading-day page. The limit means the most recent available trading days, not calendar days. Official files may contain all futures products; the existing V1 importer filters the data to TMF. Downloaded raw data and `download_manifest.json` remain local under `data/raw/taifex/` and are ignored by git. Network availability and official page structure can affect syncing, so manual `import-taifex` remains supported as a fallback.

The `import-taifex` command imports local TAIFEX CSV/ZIP files into cleaned daily tick Parquet files. The `build-bars` command converts cleaned ticks into 1m or 5m OHLCV bar files. The `backtest` command validates `configs/v1_backtest.yaml`, loads configured bar Parquet files, calculates indicators, generates VWAP Trend signals, runs conservative next-bar-open execution, and writes `config.yaml`, `trades.csv`, `equity_curve.csv`, and `metrics.json` under `data/results/backtests/<strategy_name>/<run_id>/`. The `app backtest-lab` command starts the local Streamlit client for explicit official sync, import, bar building, strategy parameter edits, backtest runs, charts, trades, and saved result browsing.

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

## Common Issues

- A workflow step remains `⚠`: read the shared status card; later steps stay disabled until the
  current disk state validates.
- A plan becomes pending after files change: rerun Plan so its raw-directory fingerprint is current.
- Sync partially fails: retry Sync; successful valid files are reused and failed files remain
  explicit.
- A result disappears after restart: its fingerprint no longer matches the current config/data, or
  a required persisted artifact is missing or unreadable.
- A lock remains after a crash: run `tifq doctor`, then `tifq clean --apply-safe`; active owners are
  preserved.
- Streamlit appears stale during development: stop the process and restart the app before repeating
  acceptance checks.
