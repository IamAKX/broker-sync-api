---
id: glossary.inception-group-b
title: Inception Group B codes (gap areas)
category: glossary
load: on-demand
triggers: [gap, gaps, gap-up, gap-down, "gap up", "gap down", unfilled, filled, GUP, GDN]
order: 40
---
Pattern: `<DAY|WEEK> <UF|FD> <GUP|GDN> <1|2|3>`, for example `DAY UF GUP 1`.

| Part | Meaning |
|---|---|
| DAY / WEEK | Gap measured day-to-day (previous close -> today's open) or week-to-week |
| UF / FD | **Unfilled** (price has not closed back through the pre-gap close) / **filled** |
| GUP / GDN | Gap up / gap down |
| 1 / 2 / 3 | Recency rank: 1 = most recent; 3 are kept per bucket |

A gap only opens if the % gap exceeds a configured threshold. Each code also has `<CODE> LOW`,
`<CODE> HIGH` (the gap area's price range) and `<CODE> DATE` (when it opened); the bare code means
`HIGH`. 24 codes x 3 sub-fields.

These are NOT stored anywhere and are a stateful calculation (an area stays "unfilled" depending on
every day since it opened). Do not approximate them in SQL: answer "unavailable" and say the desktop
app's HMV / View by Date shows them.
