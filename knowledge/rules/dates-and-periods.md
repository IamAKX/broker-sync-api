---
id: rules.dates-and-periods
title: Dates and periods
category: rules
load: always
order: 20
---
- Dates written like 5-7-2020 are DD-MM-YYYY (Indian format): 5 July 2020.
- In the archives (`eod_daily`, `lmv_daily`, `eod_futures_bars`) "latest" / "last close" means the
  latest date present in the data (`max(trade_date)`), not the calendar date. Keep date filters explicit.
- "Previous" always means the previous *trading* day / week / month, not the previous calendar one.
- Weeks run Monday to Sunday. Quarters are calendar quarters; half-years Jan-Jun / Jul-Dec; the
  financial year runs 1 Apr to 31 Mar.
- The data can include special weekend sessions (for example the Diwali Muhurat session on Sunday
  8 Nov 2026), which are real trading days with their own rows.
- A date with no row (weekend / holiday): use the latest bar on or before it and say which date
  the figures are for.
- Live / "right now" / "current" / "today so far" questions: answer from `lmv_live` when LIVE
  MARKET DATA says RUNNING. When it says NOT AVAILABLE, reply with kind `live_off` (the archives
  hold end-of-day data only and are not a substitute for a live question). When it says ARCHIVE
  ONLY, answer from the latest archived data instead.
