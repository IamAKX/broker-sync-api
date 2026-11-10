---
id: examples.near-52-week-high
title: Stocks near the 52-week high
category: examples
load: on-demand
triggers: [52wh, "52 week", "52-week", "52w", "52wk", "year high", "yearly high", ATH, "all time high", "all-time high", "near high"]
order: 80
---
Q: Which stocks closed near their 52-week high on 8 Nov 2026? 52WH is computed from the bars, never
read from P.High.

```sql
WITH b AS (SELECT underlying_symbol AS symbol, trade_date, close, high FROM eod_futures_bars
           WHERE symbol = underlying_symbol || '_I' AND trade_date > DATE '2026-11-08' - 400),
t AS (SELECT * FROM b WHERE trade_date = (SELECT max(trade_date) FROM b WHERE trade_date <= DATE '2026-11-08')),
h AS (SELECT symbol, max(high) AS wh52 FROM b
      WHERE trade_date > DATE '2026-11-08' - 364 AND trade_date <= DATE '2026-11-08' GROUP BY symbol)
SELECT t.symbol, t.trade_date, t.close, h.wh52, round(100.0 * (t.close - h.wh52) / h.wh52, 2) AS pct_vs_52wh
FROM t JOIN h USING (symbol) WHERE t.close >= h.wh52 * 0.98 ORDER BY pct_vs_52wh DESC LIMIT 50
```

For one stock's 52WH, filter both CTEs to that symbol. The same pattern gives 52WL with `min(low)`.
