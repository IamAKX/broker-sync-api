---
id: glossary.formula-builder
title: Formula Builder codes (MT, MB, DT, DB, pivots, ATP, turnover)
category: glossary
load: always
order: 20
---
These are PRICE levels (not turnover) unless stated. Use these exact meanings and never invent
others. All 55 are keys of `lmv_daily.metrics` for 21 Jul 2026 onwards. For earlier dates they are
NOT stored, but can be computed from the daily bars in `eod_futures_bars` (say the result is
computed from futures closes, not read from the grid). Turnover / ATP codes are only reliable where
the archive has them.

**Current / expiry-period ranges**

| Code | Meaning |
|---|---|
| CMH / CML | Current month high / low (to the previous trading day in LMV) |
| CWH / CWL | Current week high / low (same rule; `''` on a week's first trading day) |
| CMO / CWO | Current month / week open (first trading day's open) |
| EWH / EWL | Expiry-week high / low: the last fully completed expiry week (the week with the month's last Tuesday) |
| RWH / RWL | Rollover-week high / low: the week after the expiry week (to date) |
| FH / FL | Previous week's last trading day high / low |

**Previous periods**

| Code | Meaning |
|---|---|
| PMH / PML / PMC | Previous month high / low / close |
| PWH / PWL / PWC | Previous week high / low / close |

**Tops and bottoms**

| Code | Meaning |
|---|---|
| DT / DB | Highest / lowest CLOSE of the 5 trading days before the date |
| WT / WB | Higher / lower of the closes on the last trading day of each of the 3 previous weeks |
| MT / MB | **Month Top / Month Bottom**: higher / lower of the closes on the last trading day of each of the 2 previous calendar months. NOT "Month Turnover" |

**Camarilla pivots** (range = previous high - previous low; C = previous close; H, L = previous high, low)

| Code | Meaning |
|---|---|
| DR3 / DR4 / DR6 | Daily resistance: C + range*1.1/4, C + range*1.1/2, (H/L)*C (previous day) |
| DS3 / DS4 / DS6 | Daily support: C - range*1.1/4, C - range*1.1/2, C - (R6 - C) |
| WR3/WR4/WR6, WS3/WS4/WS6 | The same from the previous **week's** H/L/C |
| MR3/MR4/MR6, MS3/MS4/MS6 | The same from the previous **month's** H/L/C |
| DAY PIVOT / WEEK PIVOT / MONTH PIVOT | (previous H + L + C) / 3 for the previous day / week / month |

**Average traded price (ATP)**

| Code | Meaning |
|---|---|
| PATP | Previous day's average traded price |
| CWATP / PWATP | Average of the daily avg rates: current / previous week |
| CMATP / PMATP | The same: current / previous month |

**Turnover (crore) weighted by the size of the move** (not the same as raw `TurnOver`)

| Code | Meaning |
|---|---|
| DAY TO | volume * avg_rate / 10,000,000 * abs(day % change) / 100 |
| PDTO | DAY TO of the previous trading day |
| CWTO / PWTO | The same for the week to date / the previous week |

**% change**

| Code | Meaning |
|---|---|
| WEEK % CHANGE | (Close - previous week close) / previous week close * 100 |
| MONTH % CHANGE | (Close - previous month close) / previous month close * 100 |
