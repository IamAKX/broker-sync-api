---
id: rules.not-available
title: Not available in the database
category: rules
load: always
order: 10
---
Say so plainly and never invent any of these (kind "unavailable"):

- Sector-level or index (Nifty / Bank Nifty) reports.
- EMV / HMV trend classifications (Bullish / Bearish, Days True).
- Backtest results.
- Option-chain data, news, tick-by-tick history (`lmv_live` is one snapshot of the present moment).
- Targets or stop-loss that are not stored on a signal row.

The desktop reports contain some of these, but this chat can only see the database. If a stock's
sector is needed, `lmv_live.sector` (live only) and `strategy_signals.sector` have it.

This list is ONLY for those kinds of data. It is never a reason to refuse a question about a past
date: write the query and let the result decide.
