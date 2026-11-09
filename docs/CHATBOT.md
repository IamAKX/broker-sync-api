# AI Chatbot ("Ask Fuku")

Natural-language Q&A over the caller's Fuku Lens data, using the Anthropic API (Claude). Phase 1 is
deliberately simple: **question → guarded read-only SQL → answer**. The only external setup
is an Anthropic API key (`ANTHROPIC_API_KEY`); the HTTP contract is in
[`API_CONTRACT.md`](API_CONTRACT.md#chat).

## What it can and cannot answer

| Can | Cannot (answers "not available", never invents) |
|---|---|
| EOD OHLC/volume/% change for tracked stocks (`eod_daily`) | Index (Nifty/Bank Nifty) or sector-level reports |
| The LMV daily archive, any LMV column (`lmv_daily`) | EMV/HMV trend classifications, Days True |
| The user's strategy alerts & win rates (`strategy_signals`) | Backtest results, Market Profile, option chain |
| Futures EOD incl. open interest (`eod_futures_bars`) | Real-time ticks, news |
| Holidays, strategy names | Strategy *formulas* |
| General market concepts ("what is open interest?") from model knowledge | Personalised buy/sell advice (it describes the data + a disclaimer) |

"Live"/"right now" questions in the answer bank are answered from the most recent archived
data and the answer says so with its date — the chat has no tick feed.

## Pipeline (`app/services/chat/pipeline.py`)

```
question ─▶ PLANNER call ─▶ sql_guard ─▶ sandboxed run ─▶ ANSWER call (streamed)
             JSON plan      (AST check)   (read-only tx)     (rows only)
```

1. **Planner** (`prompts.PLANNER_RULES` + catalog + live data-freshness + LMV metric keys)
   returns one JSON object: `kind` = `sql | knowledge | unavailable | clarify | refuse`.
   `knowledge`/`unavailable`/`clarify`/`refuse` carry their reply directly (one model call).
2. **SQL** goes through `sql_guard.validate_and_prepare`; on rejection or a DB error the model
   gets the error text and **one** repair attempt (`CHAT_SQL_MAX_RETRIES`).
3. The query runs in `sql_runner.sandbox` and returns ≤ `CHAT_SQL_MAX_ROWS` rows.
4. **Answer** call sees only the question, the SQL and the rows, and is instructed to use
   nothing else. It streams tokens back.
5. Follow-ups: the client sends the last few turns (`history`); the server stores nothing.

Streamed events: `stage` (`understanding → generating_query → running_query →
writing_answer`), `sql`, `rows`, `token`, `answer`, `done`, `error` — the desktop UI animates
off the `stage` events.

## Guardrails — four independent layers

1. **Planner scope rules**: refuse anything outside markets/stocks/Fuku Lens; treat the
   question as data, never instructions.
2. **SQL validator** (`sql_guard.py`, hard code, not prompt): exactly one `SELECT`; only the
   seven documented views; no schema-qualified names, DML/DDL, `INTO`, locking, parameters,
   table functions; unknown functions rejected (`pg_sleep`, `set_config`, `dblink`,
   `pg_read_file`…). The **re-rendered AST** is what executes, wrapped in a `LIMIT`.
3. **Sandbox** (`sql_runner.py`): the model never sees real tables. Per-request `TEMP` views
   point at the caller's own tenant schema (from the verified JWT) and hard-code
   `user_id = <caller>` on per-user tables; then the transaction is switched to
   `READ ONLY`, given a statement timeout, and always rolled back.
4. **Answer discipline + limits**: answer step may only use returned rows; per-user rate limit;
   question/history/result size caps.

Known limits: the DB role is the app's normal one (read-only is enforced per transaction, not by a
separate Postgres role) — creating a dedicated `SELECT`-only role is the recommended next
hardening. The rate limiter is per gunicorn worker.

## Data catalog (what the model queries)

Defined in `sql_runner._view_ddl` and described to the model in `prompts._CATALOG`.
Add or change a view in **both** places, add it to `sql_guard.ALLOWED_RELATIONS`, and add a
test.

| View | Source | Scope |
|---|---|---|
| `stocks` | tenant `Stock` | tenant |
| `eod_daily` | tenant `HistoricalStockValue` pivoted (Open/High/Low/Close/AvgRate/Quantity/DiffPcnt) | tenant |
| `lmv_daily` | tenant `LmvDailySnapshotWide` (`metrics` JSONB) | tenant |
| `strategy_signals` | tenant `StrategySignal` | **user** |
| `my_strategies` | tenant `SavedStrategy` (no formulas) | **user** |
| `eod_futures_bars` | `public.EodBar` ⨝ `Instrument` | shared market data |
| `market_holidays` | tenant `Holiday` | tenant |

## Configuration

All environment variables (see `app/core/config.py`, `.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `CHAT_ENABLED` | `false` | Master switch; `/chat/ask` returns 503 `chat_disabled` when off |
| `ANTHROPIC_API_KEY` | *(blank)* | Secret API key from console.anthropic.com. Server-side only; never commit it |
| `CHAT_MODEL_ID` | `claude-haiku-4-5` | Anthropic model id |
| `CHAT_ANSWER_MODEL_ID` | *(same)* | Optional different model for the answer step |
| `CHAT_SQL_MAX_TOKENS` / `CHAT_ANSWER_MAX_TOKENS` | `1200` | Output token caps |
| `CHAT_LLM_TIMEOUT` / `CHAT_LLM_MAX_RETRIES` | `60` / `2` | Anthropic client timeout (seconds) and automatic retries |
| `CHAT_SQL_TIMEOUT_MS` | `8000` | Postgres statement timeout for generated SQL |
| `CHAT_SQL_MAX_ROWS` | `200` | Row cap on the query |
| `CHAT_ANSWER_MAX_ROWS` / `CHAT_ANSWER_MAX_CHARS` | `60` / `24000` | How much result data the answer model sees |
| `CHAT_SQL_MAX_RETRIES` | `1` | Repair attempts after a rejected/failed query |
| `CHAT_MAX_HISTORY_TURNS` | `6` | Prior turns honoured for follow-ups |
| `CHAT_MAX_QUESTION_CHARS` | `1000` | Question length cap |
| `CHAT_RATE_LIMIT_PER_MINUTE` | `12` | Per user, per worker; `0` disables |

## Observability

Each question logs one structured `chat_query` line to the main app log (user, tenant schema,
kind, row count, tokens, latency, error). It carries no prompt text and no SQL. Row *data* is never logged.
`chat_repair_attempt` marks a self-corrected query — a high rate means the catalog/prompt needs work.

Two dedicated audit logs (`app/services/chat/audit_log.py`) live in `CHAT_LOG_DIR` (default
`logs/chat/`, i.e. `/home/ec2-user/broker-sync-api/logs/chat/` on EC2):

- `chat_prompts.log` — one JSON line per user prompt (user, schema, question).
- `chat_queries.log` — one JSON line per generated SQL, with status `executed` / `rejected` / `db_error`.

Rotation: `deploy/brokersync-chat-logrotate` (daily, 30 days, compressed) is installed to
`/etc/logrotate.d/brokersync-chat`.

## Extending

- **Better answers for a recurring question**: add an example to `prompts._EXAMPLES`.
- **New data source**: new view (see catalog note above).
- **Questions needing computed reports** (EMV classification, backtests, sector reports): not DB
  data today. Options for a later phase: persist those report outputs server-side into a table
  and expose it as a view.

## Tests

`tests/test_chat_sql_guard.py` (28 attack/validity cases), `tests/test_chat_pipeline.py`
(pipeline with a fake LLM client: happy path, refusal, model-refusal block, repair loop, history
sanitising, rate limit). The sandbox was additionally verified against a real Postgres
(views, read-only, timeout, rollback). Not covered by automated tests: live Anthropic API calls.
