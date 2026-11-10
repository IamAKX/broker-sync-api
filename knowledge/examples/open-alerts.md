---
id: examples.open-alerts
title: Open strategy alerts
category: examples
load: always
order: 30
---
Q: Which of my strategy alerts are still open?

```sql
SELECT strategy_name, symbol, direction, entry_time, entry_price FROM strategy_signals
WHERE status = 'open' ORDER BY entry_time DESC
```
