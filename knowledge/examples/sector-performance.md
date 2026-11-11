---
id: examples.sector-performance
title: Stocks of one sector, and sector ranking
category: examples
load: always
order: 15
---
Q: Which banking stocks performed well on the latest trading day?

```sql
SELECT e.symbol, ss.sector, e.close, e.diff_pct, e.trade_date
FROM eod_daily e JOIN stock_sectors ss ON ss.symbol = e.symbol
WHERE ss.sector = 'BANKING' AND e.trade_date = (SELECT max(trade_date) FROM eod_daily)
ORDER BY e.diff_pct DESC LIMIT 20
```

Q: Which sector did best on the latest trading day?

```sql
SELECT ss.sector, count(*) AS stocks, round(avg(e.diff_pct), 2) AS avg_pct_change, max(e.trade_date) AS trade_date
FROM eod_daily e JOIN stock_sectors ss ON ss.symbol = e.symbol
WHERE e.trade_date = (SELECT max(trade_date) FROM eod_daily) AND ss.sector <> 'INDEX'
GROUP BY ss.sector ORDER BY avg_pct_change DESC LIMIT 30
```
