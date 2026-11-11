---
id: examples.live-movers
title: Live market questions
category: examples
load: always
order: 5
---
(Only when LIVE MARKET DATA says RUNNING.)

Q: Which stocks are falling the most right now?

```sql
SELECT symbol, sector,
       NULLIF(metrics->>'Current', '')::numeric AS current_price,
       NULLIF(metrics->>'% Change', '')::numeric AS pct_change
FROM lmv_live WHERE NULLIF(metrics->>'% Change', '') IS NOT NULL
ORDER BY pct_change ASC LIMIT 10
```

Q: What is Reliance trading at now?

```sql
SELECT symbol, NULLIF(metrics->>'Current', '')::numeric AS current_price,
       NULLIF(metrics->>'% Change', '')::numeric AS pct_change,
       NULLIF(metrics->>'High', '')::numeric AS day_high,
       NULLIF(metrics->>'Low', '')::numeric AS day_low
FROM lmv_live WHERE symbol = 'RELIANCE' OR display_name ILIKE '%reliance%' LIMIT 5
```

Q: Which sector is strongest in the live market?

```sql
SELECT sector, count(*) AS stocks,
       round(avg(NULLIF(metrics->>'% Change', '')::numeric), 2) AS avg_pct_change
FROM lmv_live WHERE sector IS NOT NULL GROUP BY sector ORDER BY avg_pct_change DESC LIMIT 20
```
