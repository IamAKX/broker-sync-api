---
id: examples.open-alerts
title: Open strategy alerts
category: examples
load: always
order: 30
---
Q: Which of my strategy alerts are still open? / Show the live alerts triggered by my strategies.
(Works whether or not the Live Master View is running.)

```sql
SELECT strategy_name, symbol, direction, entry_time, entry_price, count(*) OVER () AS total_open
FROM strategy_signals WHERE status = 'open' ORDER BY entry_time DESC LIMIT 50
```

Q: How many alerts are open per strategy?

```sql
SELECT strategy_name, direction, count(*) AS open_alerts, max(entry_time) AS latest_entry
FROM strategy_signals WHERE status = 'open' GROUP BY strategy_name, direction ORDER BY open_alerts DESC LIMIT 50
```

Q: How are my open alerts doing right now? (only when LIVE MARKET DATA says RUNNING)

```sql
SELECT s.strategy_name, s.symbol, s.direction, s.entry_time, s.entry_price,
       NULLIF(l.metrics->>'Current', '')::numeric AS current_price,
       round(CASE WHEN s.direction = 'BUY' THEN 1 ELSE -1 END
             * (NULLIF(l.metrics->>'Current', '')::numeric - s.entry_price) / NULLIF(s.entry_price, 0) * 100, 2) AS pct_since_entry,
       count(*) OVER () AS total_open
FROM strategy_signals s LEFT JOIN lmv_live l ON l.symbol = s.symbol
WHERE s.status = 'open' ORDER BY s.entry_time DESC LIMIT 50
```
