---
id: glossary.raw-columns
title: Raw LMV columns
category: glossary
load: always
order: 10
---
These are keys of `lmv_daily.metrics` (21 Jul 2026 onwards). Some are also in `eod_daily` /
`eod_futures_bars`.

| Code | Meaning | Where |
|---|---|---|
| Scrip Name | Stock symbol | `lmv_daily.symbol` / `display_name` |
| Lot Size | F&O lot size | `metrics->>'Lot Size'` |
| % Change | Day % change | `metrics->>'% Change'`; `eod_daily.diff_pct` |
| Current | Live last traded price | `metrics->>'Current'` |
| Open / High / Low / Close | Day open / high / low / close | `metrics->>'Open'` etc.; `eod_daily`; `eod_futures_bars` |
| Avg Rate | Average traded price for the day | `metrics->>'Avg Rate'`; `eod_daily.avg_rate` |
| OI Difference Percentage | % change in open interest | `metrics->>'OI Difference Percentage'` |
| P.High / P.Low | **Previous trading day's** high / low. NOT 52-week figures | `metrics->>'P.High'` / `'P.Low'` |
| P.Quantity | Previous day's traded quantity | `metrics->>'P.Quantity'` |
| Qty | Today's traded quantity | `metrics->>'Qty'`; `eod_daily.volume` |
| TurnOver | Raw turnover from the broker file | `metrics->>'TurnOver'` |
| callstrikehighestoi | Call strike with the highest open interest | `metrics->>'callstrikehighestoi'` |
| Callstrikewithsecondhighestoi | Call strike with the 2nd-highest OI | `metrics->>'Callstrikewithsecondhighestoi'` |
| PutStrikeWithsecondHighestOI | Put strike with the 2nd-highest OI | `metrics->>'PutStrikeWithsecondHighestOI'` |
| TodayPutHighestStrike | Put strike with the highest OI today | `metrics->>'TodayPutHighestStrike'` |
| Max Pain | Option max-pain strike | `metrics->>'Max Pain'` |
| VAH / VAL / POC | Market Profile value-area high / low / point of control | `metrics->>'VAH'` / `'VAL'` / `'POC'` |
| OR.High / OR.Low | The day's opening-range high / low | `metrics->>'OR.High'` / `'OR.Low'` |
| pdhighestcalloi, pdhighestputoi, pdsecondhighestcalloi | Previous day's highest call OI / highest put OI / 2nd-highest call OI (meaning taken from the key names; say so if unsure) | `metrics->>'<key>'` |
