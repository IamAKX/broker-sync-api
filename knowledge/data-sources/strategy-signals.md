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
Alerts fired by the user's own Strategy Notifications (only this user's rows).

- `direction` is 'BUY' or 'SELL'.
- `status` is one of: open, stopped_out, all_targets_achieved, trade_cancelled, intraday_closed.
- `event_time` = when it last changed (`resolved_at` if resolved, else `entry_time`).
- Yield % of a closed signal: BUY -> (exit_price-entry_price)/entry_price*100,
  SELL -> (entry_price-exit_price)/entry_price*100 (only where `exit_price` is not null).
- `sector` lives only here; it is the only place a stock's sector can be found.
