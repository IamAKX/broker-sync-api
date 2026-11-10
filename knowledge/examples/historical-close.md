---
id: examples.historical-close
title: Close on an old date
category: examples
load: on-demand
triggers: [close, closed, price, history, historical]
order: 60
---
Q: What was RELIANCE's close on 7 May 2020? (older than the archive, so `eod_futures_bars`)

```sql
SELECT trade_date, symbol, close, volume, open_interest FROM eod_futures_bars
WHERE underlying_symbol = 'RELIANCE' AND symbol = 'RELIANCE_I'
ORDER BY abs(trade_date - DATE '2020-05-07') LIMIT 3
```
