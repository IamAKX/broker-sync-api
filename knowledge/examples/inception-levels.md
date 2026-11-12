---
id: examples.inception-levels
title: Inception levels (QT, PQC, CQO, YT ...) for stocks, archived and live
category: examples
load: always
order: 8
---
Q: Any stocks within 1 percent of QT? (LIVE MARKET DATA says RUNNING: live price from `lmv_live`,
QT from the latest `inception_levels` row. Never calculate QT.)

```sql
SELECT l.symbol, l.sector,
       NULLIF(l.metrics->>'Current', '')::numeric AS current_price,
       v.qt, v.trade_date AS levels_as_of,
       round(100.0 * (NULLIF(l.metrics->>'Current', '')::numeric - v.qt) / v.qt, 2) AS pct_from_qt
FROM lmv_live l JOIN inception_levels v USING (symbol)
WHERE v.trade_date = (SELECT max(trade_date) FROM inception_levels)
  AND v.qt IS NOT NULL AND NULLIF(l.metrics->>'Current', '') IS NOT NULL
  AND abs(NULLIF(l.metrics->>'Current', '')::numeric - v.qt) / v.qt * 100 <= 1
ORDER BY abs(NULLIF(l.metrics->>'Current', '')::numeric - v.qt) / v.qt LIMIT 50
```

Q: The same question when live data is NOT running or the user asks about the last close / a date
(here: as of 9 Nov 2026):

```sql
SELECT symbol, trade_date, close, qt, round(100.0 * (close - qt) / qt, 2) AS pct_from_qt
FROM inception_levels
WHERE trade_date = (SELECT max(trade_date) FROM inception_levels WHERE trade_date <= DATE '2026-11-09')
  AND qt IS NOT NULL AND abs(close - qt) / qt * 100 <= 1
ORDER BY abs(close - qt) / qt LIMIT 50
```

Q: What are the QT, QB, PQC and 52WH of ZYDUSLIFE?

```sql
SELECT symbol, trade_date, close, qt, qb, pqc, wh52
FROM inception_levels
WHERE symbol = 'ZYDUSLIFE' AND trade_date = (SELECT max(trade_date) FROM inception_levels)
```
