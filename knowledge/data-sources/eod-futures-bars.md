---
id: data-source.eod-futures-bars
title: eod_futures_bars
category: data-source
load: always
order: 60
---
```
eod_futures_bars(symbol, underlying_symbol, instrument_type, trade_date, open, high, low, close,
                 volume, open_interest, avg_rate)
```
Continuous-futures end-of-day history shared across all users. **Long history**: daily bars from
12 Jun 2000 to the latest date for ~227 underlyings (most go back to 2000-2001; RELIANCE from
9 Nov 2001).

- `symbol` like 'ADANIENT_I' is the near-month series, 'ADANIENT_II' the next month;
  `underlying_symbol` is 'ADANIENT'. For one stock use
  `underlying_symbol = 'RELIANCE' AND symbol = 'RELIANCE_I'`; for all stocks
  `symbol = underlying_symbol || '_I'`.
- Use THIS view for any historical price / volume / open-interest question about a date before the
  `lmv_daily` / `eod_daily` earliest dates. Say the figures are futures prices.
- Do NOT calculate an Inception Group A code (52WH, ATH, QT, PQC, CQO, YT, ...) from these bars:
  read its column from `inception_levels`, which already holds the app's exact value.
- Never claim data "only goes back to" a date without checking `min(trade_date)` here.
- Weekend / holiday dates have no row: use the latest bar on or before the date and say so.
- Other columns exist on the underlying table (patp, day_to, max_pain, vah, poc, or_high, ...) but
  are not exposed in this view.
