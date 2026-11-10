---
id: examples.strategy-win-rate
title: Win rate per strategy
category: examples
load: on-demand
triggers: ["win rate", winrate, "success rate", "hit rate", yield, profit, "closed signals"]
order: 50
---
Q: What is the win rate of my closed BUY signals by strategy?

```sql
SELECT strategy_name, count(*) AS trades,
  round(100.0 * count(*) FILTER (WHERE exit_price > entry_price) / count(*), 1) AS win_pct
FROM strategy_signals WHERE direction = 'BUY' AND status <> 'open' AND exit_price IS NOT NULL
GROUP BY strategy_name ORDER BY trades DESC
```
