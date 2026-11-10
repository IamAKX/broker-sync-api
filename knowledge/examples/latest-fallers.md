---
id: examples.latest-fallers
title: Biggest fallers on the latest day
category: examples
load: always
order: 10
---
Q: Which stocks fell the most on the latest trading day?

```sql
SELECT symbol, close, diff_pct FROM eod_daily
WHERE trade_date = (SELECT max(trade_date) FROM eod_daily) ORDER BY diff_pct ASC LIMIT 10
```
