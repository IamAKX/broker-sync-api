---
id: examples.near-52-week-high
title: Stocks near the 52-week high
category: examples
load: on-demand
triggers: [52wh, 52wl, "52 week", "52-week", "52w", "52wk", "year high", "yearly high", "year low", ATH, ATL, "all time high", "all-time high", "all time low", "near high"]
order: 80
---
Q: Which stocks closed near their 52-week high on 8 Nov 2026? 52WH is read from `inception_levels`
(`wh52`), never calculated and never taken from P.High.

```sql
SELECT symbol, trade_date, close, wh52, round(100.0 * (close - wh52) / wh52, 2) AS pct_vs_52wh
FROM inception_levels
WHERE trade_date = (SELECT max(trade_date) FROM inception_levels WHERE trade_date <= DATE '2026-11-08')
  AND close >= wh52 * 0.98
ORDER BY pct_vs_52wh DESC LIMIT 50
```

For one stock add `AND symbol = 'RELIANCE'`. The same pattern gives 52WL (`wl52`), ATH (`ath`) and
ATL (`atl`).
