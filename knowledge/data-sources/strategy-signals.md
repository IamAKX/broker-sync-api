---
id: data-source.strategy-signals
title: strategy_signals
category: data-source
load: always
order: 40
---
```
strategy_signals(strategy_name, symbol, sector, direction, status, entry_time, entry_price,
                 exit_price, resolved_at, running_high, running_low, score, event_time,
                 risk_reward jsonb, metrics jsonb)
```
Alerts fired by the user's own Strategy Notifications (only this user's rows). The desktop writes
a row the moment an alert fires and updates it when a target is hit, it is stopped out, cancelled
or closed — so this view is current during market hours and does NOT depend on the Live Master
View being available to this chat. "Live alerts", "active alerts", "open alerts", "alerts
triggered now / today" are all answered from here (never kind `live_off`).

- `direction` is 'BUY' or 'SELL'.
- `status` is one of: open, stopped_out, all_targets_achieved, trade_cancelled, intraday_closed.
- `event_time` = when it last changed (`resolved_at` if resolved, else `entry_time`).
- Yield % of a closed signal: BUY -> (exit_price-entry_price)/entry_price*100,
  SELL -> (entry_price-exit_price)/entry_price*100 (only where `exit_price` is not null).
- "Open" / "active" / "live" / "running" alert = `status = 'open'`. "Today" = `entry_time::date` equal
  to the calendar date under DATA FRESHNESS.
- There can be hundreds of open alerts. Never present the number of returned rows as the total:
  select `count(*) OVER () AS total` (or a separate GROUP BY summary) and say the list is the most
  recent N of that total.
- `running_high` / `running_low` are the extremes seen since entry, as of the alert's last update;
  they are not a live price. For the current price of an alert's stock, join `lmv_live` on symbol
  when LIVE MARKET DATA says RUNNING; otherwise give the entry details and say the current price
  is not available because the Live Master View is not running.
- `sector` is the sector recorded on the alert; for sector membership in general use `stock_sectors`.
