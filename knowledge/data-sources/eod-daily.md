---
id: data-source.eod-daily
title: eod_daily
category: data-source
load: always
order: 20
---
`eod_daily(trade_date date, symbol, open, high, low, close, avg_rate, volume, diff_pct)`

Daily end-of-day OHLC for the tracked stocks, one row per (trade_date, symbol). **Short history**
(see DATA FRESHNESS for the earliest date; 4 May 2026 at time of writing). For anything older use
`eod_futures_bars`.

- `avg_rate` = average traded price; `volume` = traded quantity.
- `diff_pct` = the day's percentage change as supplied by the broker file (-1.25 means -1.25%).
