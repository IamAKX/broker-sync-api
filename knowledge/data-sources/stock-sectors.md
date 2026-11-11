---
id: data-source.stock-sectors
title: stock_sectors
category: data-source
load: always
order: 15
---
`stock_sectors(symbol, sector)`

Which sector each stock belongs to: the user's own Sector Stock list from the desktop's Config
Editor (the same one the Live Master View uses). One row per stock; `symbol` matches the `symbol`
column of every other view; `sector` is UPPERCASE.

- This is the ONLY source of sector membership for the archives. Join it for every sector
  question; never list a sector's stocks from memory and never guess a stock's sector.
- Use the exact sector names listed under DATA FRESHNESS ("Sectors in stock_sectors"). The user's
  wording may differ (Automobile = AUTO, IT / software = TECHNOLOGY, banks = BANKING, capital
  goods = CG); pick the matching name.
- The INDEX sector holds indices (NIFTY, BANKNIFTY, ...), not stocks; leave it out of stock rankings.
- A stock missing from this view has no sector assigned: say so instead of assigning one.
