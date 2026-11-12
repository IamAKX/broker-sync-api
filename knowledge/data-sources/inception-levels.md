---
id: data-source.inception-levels
title: inception_levels
category: data-source
load: always
order: 55
---
```
inception_levels(symbol, trade_date, open, high, low, close, volume, open_interest,
                 <one column per Inception Group A code: qt, qb, pqc, wh52, ath, cqo, ...>)
```
**The only source for the Inception Group A codes** (52WH, 52WL, ATH, ATL, quarter / half-year /
year / financial-year highs, lows, opens, closes, tops and bottoms, previous-day values). One row
per (stock, trading day) with every code already calculated exactly as the app's Inception screens
(View by Date / HMV) show it. The glossary lists the column for each code.

- **Read the column. Never work one of these codes out yourself** from `eod_futures_bars` or any
  other view, however simple it looks: the app's definitions are precise (QT is not "the highest
  price of the quarter") and a hand-written version gives numbers that disagree with the app.
- Values are futures prices (near-month continuous series), history from 2000. Say so.
- `symbol` is the plain stock symbol as the user's grid spells it ('RELIANCE', 'M&M'), so it joins
  directly to `lmv_live`, `lmv_daily`, `eod_daily` and `stock_sectors` with `USING (symbol)`.
- **Always pin the rows**: either one date for many stocks
  (`trade_date = (SELECT max(trade_date) FROM inception_levels)` for the latest, or
  `trade_date = (SELECT max(trade_date) FROM inception_levels WHERE trade_date <= DATE '...')` for a
  past date, which also handles weekends / holidays), or one `symbol` over a date range of at most
  a year or two. An unfiltered scan of this view is too slow and will time out.
- Select only the code columns the question needs.
- A value is NULL when the app shows a blank (for example PQC for a stock that did not trade in
  the previous quarter).
- Comparing the price with a level ("near QT", "above PQC") when no date is given:
  - LIVE MARKET DATA says RUNNING: the level comes from the latest row here and the price is the
    live one, `lmv_live` `metrics->>'Current'`, joined on `symbol` (even if the user did not type
    "live": they are looking at the live market). This is the one case where `lmv_live` is joined
    to another view.
  - otherwise (or for a past date): use this view's own `close` from the same row as the level.
  - Never take the price from `eod_daily` or `lmv_daily` for such a comparison: their latest date
    can differ from this view's and they are not the futures series the levels are built from.
- Column aliases must be valid identifiers: never start one with a digit (`wh52` or
  `week52_high`, not `52_week_high`).
