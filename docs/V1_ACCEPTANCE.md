# V1 Final Release Acceptance

Task 13 validates V1 as a reproducible local TMF day-session backtesting tool. It does not authorize
V2, live data, broker integration, paper trading, or order placement.

## Release Gates

- Windows-safe PID and operation-lock handling using PID plus process creation time.
- Engine-enforced 13:35 force-flat, pre-13:35 fallback, no late entries, and no overnight position.
- Partial-sync warning semantics and manifest/hash-backed workflow restart recovery.
- True no-op second import and bar build without rewriting unchanged Parquet.
- `ruff`, `mypy`, and the complete pytest suite passing locally.
- GitHub Actions passing on Windows and Ubuntu with Python 3.11.
- Real official TAIFEX sync smoke test passing twice without duplicate download or output rewrite.
- Real browser click-through of all eight workflow actions, restart recovery, result loading,
  diagnostics, screenshots, and zero unhandled terminal tracebacks.
- Deterministic Python Playwright E2E passing on Windows Chromium in GitHub Actions.

## Evidence

Local evidence is generated under ignored paths:

```text
artifacts/ui/v1-final/
artifacts/validation/v1-final/report.json
artifacts/validation/v1-final/terminal.log
artifacts/validation/v1-final/browser-report.json
artifacts/validation/v1-final/browser-terminal.log
artifacts/validation/v1-final/playwright-trace.zip
```

The validation report records environment versions, commit SHA, quality checks, official-data and
workflow timings, no-op counts, browser actions, restart restoration, screenshots, and terminal
error count. Runtime market data, manifests, Parquet files, screenshots, and logs are not committed.

Run the automated gates with:

```powershell
python -m ruff check .
python -m mypy src
python -m pytest -q -m "not e2e"
python -m pytest -q tests/e2e --browser chromium --tracing retain-on-failure
```

## Known V1 Limits

- TMF only; day session only; positions limited to -1, 0, or +1.
- Historical local/public official TAIFEX data only; no live feed or broker API.
- Continuous front month is unadjusted and indicators warm up again after each contract segment.
- Strategy fills use next-bar-open except deterministic session/contract risk exits.
- Backtest results are research evidence, not financial advice or a guarantee of future performance.
