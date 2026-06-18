# VWAP Trend Strategy

The V1 VWAP Trend strategy emits signals only. It must not place orders, mutate portfolio state, or access broker state.

Entries depend on VWAP, fast and slow EMA alignment, ATR filters, and session time limits. Exits are handled by the backtest engine using stop loss, take profit, reverse signal, force flatten, and no-overnight rules.

