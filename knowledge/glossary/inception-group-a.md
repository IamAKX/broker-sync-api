---
id: glossary.inception-group-a
title: Inception Group A codes (52WH, 52WL, ATH, P.*, quarter / half-year / year)
category: glossary
load: always
order: 30
---
None of these is stored in any table, and there is NO 52WH / 52WL / ATH / ATL key in
`lmv_daily.metrics`. ALWAYS compute them from the daily bars in `eod_futures_bars`
(near-month series, `symbol = underlying_symbol || '_I'`), for ANY date, recent or old, and say so.
Use the same method every time so repeated questions agree. The example queries show the pattern.

| Code | Meaning | Computed from |
|---|---|---|
| P.OPEN / P.HIGH / P.LOW / P.CLOSE | Previous day open / high / low / close | `lag(open/high/low/close)` |
| P.QTY / P.OI | Previous day volume / open interest | `lag(volume)` / `lag(open_interest)` |
| % CHG PDC AND OPEN | % change, previous day close -> today's open | `lag(close)` vs `open` |
| % CHG PWC AND OPEN | % change, previous week close -> current week open | previous week's last `close` vs this week's first `open` |
| DAY % CHANGE | % change, previous close -> today's close | `lag(close)` vs `close` |
| **52WH / 52WL** | Highest high / lowest low over the trailing **364 calendar days** including the date (the standard 52-week high / low) | `max(high)` / `min(low)` where `trade_date > d - 364 and <= d` |
| ATH / ATL | Highest high / lowest low over all history | `max(high)` / `min(low)` up to the date |
| CQH / CQO / CQL | Current quarter high / open / low | max(high) / first `open` / min(low) in the calendar quarter to date |
| PQH / PQO / PQL / PQC | Previous quarter high / open / low / close | the same for the previous quarter; close = its last `close` |
| QT / QB | Quarter top / bottom: max / min of PQC over the last 2 quarters | |
| CHYH / CHYO / CHYL | Current half-year high / open / low | half-year to date |
| PHYH / PHYO / PHYL / PHYC | Previous half-year high / open / low / close | |
| HYT / HYB | Half-year top / bottom: max / min of PHYC over the last 2 half-years | |
| CYH / CYO / CYL | Current calendar year high / open / low | year to date |
| PYH / PYO / PYL / PYC | Previous year high / open / low / close | |
| YT / YB | Year top / bottom: max / min of PYC over the last 2 years | |
| CFYH / CFYO / CFYL | Current financial year (1 Apr - 31 Mar) high / open / low | |
| PFYH / PFYO / PFYL / PFYC | Previous financial year high / open / low / close | |

"Near / close to the 52-week high" means close within about 2% below the 52WH unless the user says
otherwise. Never use `P.High` or the day's High as a 52-week figure.
