---
id: glossary.inception-group-a
title: Inception Group A codes (52WH, 52WL, ATH, P.*, quarter / half-year / year)
category: glossary
load: always
order: 30
---
Every code below is a ready-made column of the `inception_levels` view, calculated exactly as the
app shows it. ALWAYS read the column; NEVER calculate one of these yourself from the bars, and
there is no such key in `lmv_daily.metrics` / `lmv_live.metrics` unless it is listed under the
metric keys. The "Meaning" column is for explaining a code to the user, not a recipe to re-implement.

| Code | `inception_levels` column | Meaning |
|---|---|---|
| P.OPEN | `p_open` | Previous trading day's open |
| P.HIGH | `p_high` | Previous trading day's high |
| P.LOW | `p_low` | Previous trading day's low |
| P.CLOSE | `p_close` | Previous trading day's close |
| P.QTY | `p_qty` | Previous trading day's volume |
| P.OI | `p_oi` | Previous trading day's open interest |
| % CHG PDC AND OPEN | `pct_chg_pdc_open` | % change from the previous day's close to the day's open |
| % CHG PWC AND OPEN | `pct_chg_pwc_open` | % change from the previous week's close to the day's open |
| DAY % CHANGE | `day_pct_change` | % change from the previous day's close to the day's close |
| 52WH | `wh52` | 52-week high: highest high of the last 52 weeks, including the date |
| 52WL | `wl52` | 52-week low: lowest low of the last 52 weeks, including the date |
| ATH | `ath` | All-time high: highest high in all history up to the date |
| ATL | `atl` | All-time low: lowest low in all history up to the date |
| CQH | `cqh` | Current quarter high, to the date |
| CQO | `cqo` | Current quarter open (open of the quarter's first trading day) |
| CQL | `cql` | Current quarter low, to the date |
| PQH | `pqh` | Previous quarter high |
| PQO | `pqo` | Previous quarter open |
| PQL | `pql` | Previous quarter low |
| PQC | `pqc` | Previous quarter close (close of its last trading day) |
| QT | `qt` | Quarter top: the HIGHER of the closes of the last two completed quarters |
| QB | `qb` | Quarter bottom: the LOWER of the closes of the last two completed quarters |
| CHYH | `chyh` | Current half-year (Jan-Jun / Jul-Dec) high, to the date |
| CHYO | `chyo` | Current half-year open |
| CHYL | `chyl` | Current half-year low, to the date |
| PHYH | `phyh` | Previous half-year high |
| PHYO | `phyo` | Previous half-year open |
| PHYL | `phyl` | Previous half-year low |
| PHYC | `phyc` | Previous half-year close |
| HYT | `hyt` | Half-year top: the higher of the closes of the last two completed half-years |
| HYB | `hyb` | Half-year bottom: the lower of the closes of the last two completed half-years |
| CYH | `cyh` | Current calendar year high, to the date |
| CYO | `cyo` | Current calendar year open |
| CYL | `cyl` | Current calendar year low, to the date |
| PYH | `pyh` | Previous calendar year high |
| PYO | `pyo` | Previous calendar year open |
| PYL | `pyl` | Previous calendar year low |
| PYC | `pyc` | Previous calendar year close |
| YT | `yt` | Year top: the higher of the closes of the last two completed calendar years |
| YB | `yb` | Year bottom: the lower of the closes of the last two completed calendar years |
| CFYH | `cfyh` | Current financial year (1 Apr - 31 Mar) high, to the date |
| CFYO | `cfyo` | Current financial year open |
| CFYL | `cfyl` | Current financial year low, to the date |
| PFYH | `pfyh` | Previous financial year high |
| PFYO | `pfyo` | Previous financial year open |
| PFYL | `pfyl` | Previous financial year low |
| PFYC | `pfyc` | Previous financial year close |

Periods are calendar periods: quarters Jan-Mar / Apr-Jun / Jul-Sep / Oct-Dec, half-years Jan-Jun /
Jul-Dec, the financial year 1 Apr - 31 Mar. A period's open is its first trading day's open and its
close is its last trading day's close. "Top" / "bottom" codes (QT, QB, HYT, HYB, YT, YB) compare
only the two period-END closes, not every day in the period. 52 weeks = the last 364 calendar days.

"Near / close to the 52-week high" means close within about 2% below the 52WH unless the user says
otherwise; "near QT / PQC / ATH ..." means within about 1% either side unless they give a figure.
Never use `P.High` or the day's High as a 52-week figure.
