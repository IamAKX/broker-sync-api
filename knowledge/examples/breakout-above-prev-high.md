---
id: examples.breakout-above-prev-high
title: Closed above the previous day's high
category: examples
load: on-demand
triggers: [breakout, "previous high", "prev high", "above yesterday", "above previous"]
order: 40
---
Q: Which stocks closed above their previous day's high?

```sql
SELECT symbol, close, prev_high FROM (
  SELECT symbol, trade_date, close, lag(high) OVER (PARTITION BY symbol ORDER BY trade_date) AS prev_high
  FROM eod_daily) t
WHERE trade_date = (SELECT max(trade_date) FROM eod_daily) AND close > prev_high
ORDER BY close - prev_high DESC LIMIT 50
```
