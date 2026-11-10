---
id: data-source.lmv-daily
title: lmv_daily
category: data-source
load: always
order: 30
---
`lmv_daily(trade_date date, symbol, display_name, metrics jsonb)`

Daily archive of the Live Master View (LMV) grid, one row per (trade_date, symbol). **Short history**:
it only starts when archiving began (see DATA FRESHNESS for the earliest date; 21 Jul 2026 at time
of writing), so it can NOT answer older dates. It is a once-a-day archive, NOT a real-time tick feed.

- `metrics` is a JSONB object keyed by the LMV column name. Key names are case-sensitive; use only
  the keys listed under "LMV metric keys" in DATA FRESHNESS.
- Some values are **empty strings** (`''`) when a code has no value yet (for example CWH / CWL on
  the first trading day of a week). Always read numbers as `NULLIF(metrics->>'Key', '')::numeric`,
  and text as `metrics->>'Key'`.
- The glossary section explains what each key means.
