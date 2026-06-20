# V2 Task 0 — Freeze V1 and Extract the Application Boundary

V1 is frozen at tag `v1.0.0`. Its data schemas, manifests, result artifacts, strategy,
next-bar-open execution, contract selection, costs, and 13:35 flatten rule are unchanged.

## Boundary

`create_application(repository_root)` constructs environment, historical data pipeline,
workflow, backtest, and result services. CLI commands now invoke these services. The
supported Streamlit command and page remain compatible through a thin legacy entry point
and an application-owned V1 compatibility surface.

Framework-neutral DTOs define operation status, workflow state, cleanup and data-pipeline
results, prepared backtests, persisted runs, and comparisons. Runtime-only prepared data is
explicitly wrapped and never serialized as an API response.

## Regression controls

- AST architecture tests prevent framework imports in the application layer and reverse
  dependencies from core into interfaces.
- A small deterministic golden fixture freezes execution timestamps, prices, costs, exit
  reason, final equity, net PnL, and trade count.
- Existing V1 unit, integration, CLI, and real Chromium tests remain mandatory.

## Known limitation

The Streamlit screen remains the V1 reference UI and retains its internal presentation
helpers. Task 1 may replace the application compatibility surface incrementally, but must
not bypass the services or change V1 behavior.

Task 0 contains no FastAPI endpoint, React/Vue project, WebSocket, broker API, live feed,
paper trading, night session, or new strategy.

