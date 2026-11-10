---
id: examples.lmv-metric-value
title: Read an LMV code for one stock
category: examples
load: on-demand
triggers: [DT, DB, WT, WB, PMH, PML, PMC, PWH, PWL, PWC, CMH, CML, CWH, CWL, "DAY TO", PDTO, PATP, "DAY PIVOT"]
order: 90
---
Q: What are the DT and DB of DIVISLAB on 6 Nov 2026? (a recent date, so the archive has it)

```sql
SELECT trade_date, symbol,
       NULLIF(metrics->>'DT', '')::numeric AS dt,
       NULLIF(metrics->>'DB', '')::numeric AS db
FROM lmv_daily WHERE symbol = 'DIVISLAB' AND trade_date = DATE '2026-11-06'
```
