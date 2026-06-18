# V1 Backtest Lab

V1 builds a reproducible local backtesting workflow for TMF historical data.

Data flow:

```text
Raw TAIFEX data -> cleaned ticks -> parquet storage -> bars -> indicators -> signals -> backtest -> report
```

Execution rule:

```text
Signals generated after bar N closes execute at bar N+1 open.
```

