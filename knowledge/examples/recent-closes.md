---
id: examples.recent-closes
title: Recent closes of one stock
category: examples
load: always
order: 20
---
Q: How has RELIANCE closed over the last 10 sessions?

```sql
SELECT trade_date, close, diff_pct FROM eod_daily WHERE symbol = 'RELIANCE'
ORDER BY trade_date DESC LIMIT 10
```
