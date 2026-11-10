---
id: examples.month-top-bottom
title: MT / MB on any date
category: examples
load: on-demand
triggers: [MT, MB, "month top", "month bottom"]
order: 70
---
Q: What was the MT (Month Top) and MB of RELIANCE on 6 July 2020? (compute from the last trading
day's close of each of the 2 previous months)

```sql
WITH me AS (SELECT trade_date, close FROM eod_futures_bars
            WHERE underlying_symbol = 'RELIANCE' AND symbol = 'RELIANCE_I'),
m AS (SELECT max(trade_date) AS last_day FROM me
      WHERE trade_date >= date_trunc('month', DATE '2020-07-06') - interval '2 months'
        AND trade_date <  date_trunc('month', DATE '2020-07-06')
      GROUP BY date_trunc('month', trade_date))
SELECT max(close) AS mt, min(close) AS mb FROM me JOIN m ON me.trade_date = m.last_day
```
