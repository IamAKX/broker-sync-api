# Chatbot knowledge base

Markdown files the Ask Fuku chatbot reads. They are loaded and validated **once at startup**
(`app/services/chat/knowledge.py`, called from the app lifespan) and assembled into the planner
prompt for every question. To change what the bot knows, edit these files and restart — no code change.

```
knowledge/
  data-sources/   one file per queryable view: columns, coverage, gotchas      (category: data-source)
  rules/          cross-cutting rules: dates, periods, what is NOT available    (category: rules)
  glossary/       short codes and abbreviations: meaning + where they live      (category: glossary)
  examples/       one worked question -> SQL per file                           (category: examples)
```

## File format

```markdown
---
id: glossary.formula-builder     # unique; must start with "<category>."
title: Formula Builder codes
category: glossary               # data-source | rules | glossary | examples
load: always                     # always | on-demand
triggers: [gap, unfilled]        # required for on-demand: terms that pull this doc in
order: 20                        # sort within the category (default 100)
---
Markdown body.
```

- **`always`** docs go into every prompt. Keep them to what nearly every question needs.
- **`on-demand`** docs are included only when a trigger appears (case-insensitive, whole token) in
  the question or the recent user turns. Use them for niche or large material and for examples.
  Triggers should be the words a user would type (codes like `MT`, phrases like `"52 week"`).
- If the combined text passes `CHAT_KNOWLEDGE_MAX_CHARS` (default 60000) the largest on-demand docs are
  dropped first, with a `knowledge_doc_dropped_over_budget` warning. `always` docs are never dropped.
- `README.md` files are ignored by the loader.

## Adding things

| To add | Do |
|---|---|
| A new view / table | New file in `data-sources/` **and** add the view in `sql_runner.py` (the model can only query what the runner creates) |
| A new short code | Add a row to the right `glossary/` table (meaning + where it lives). New group of codes: new file |
| A new rule | `rules/` |
| A question the bot keeps getting wrong | `examples/<name>.md`: the question plus a ```` ```sql ```` block |

Every ```` ```sql ```` block must pass the SQL guard; `tests/test_knowledge_base.py` checks that, plus
front-matter, unique ids and that on-demand docs have triggers, so mistakes fail in CI rather than in
front of a user.

## What is NOT here

- Behaviour (reply format, refusal rules, tone): `app/services/chat/prompts.py`.
- Live facts that change with the data (earliest / latest dates, the list of LMV metric keys): built
  from the database on each request (`DATA FRESHNESS` in the prompt), so they can never go stale here.
- Logs: `logs/chat/chat_prompts.log` and `chat_queries.log` show what was asked and what SQL ran.
