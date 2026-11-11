---
id: data-source.lmv-live
title: lmv_live
category: data-source
load: always
order: 25
---
`lmv_live(symbol, display_name, sector, metrics jsonb)`

The user's **Live Master View (LMV) right now**: the grid open in their desktop app, sent along
with this question. One row per stock, **no date column** (every row is the present moment; the
time is given under LIVE MARKET DATA). This is the ONLY real-time source.

- It has rows only when LIVE MARKET DATA says RUNNING. When it says NOT AVAILABLE the table is
  empty: reply with kind `live_off` for a live question instead of querying it. When it says
  ARCHIVE ONLY, ignore this table completely.
- `metrics` works exactly like `lmv_daily.metrics`: keyed by the LMV column name, case-sensitive.
  Use only the keys listed under "Live metric keys" in LIVE MARKET DATA; the glossary meanings are
  the same as for `lmv_daily`. Keys not in the glossary are the user's own strategy columns: show
  their values as they are and do not guess what they mean.
- Read numbers as `NULLIF(metrics->>'Key', '')::numeric` and text as `metrics->>'Key'`. A stock
  with no value for a key simply lacks it (NULL); add `IS NOT NULL` when ranking.
- `sector` is the stock's sector, so sector questions about the live market work here
  (`GROUP BY sector`).
- Use it for: current price (`Current`), today's move so far (`% Change`), today's Open / High /
  Low, volume so far (`Qty`), open-interest change, and any other LMV code "right now".
- It is a single snapshot taken when the question was asked. It can NOT tell what a value was a
  few minutes ago or how it changed between two moments today; say so if asked.
- Do not use it for past dates: use `lmv_daily`, `eod_daily` or `eod_futures_bars` for those.
