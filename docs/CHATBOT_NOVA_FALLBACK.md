# Chatbot: switching to Amazon Nova (Claude access blocked)

Record of the 2026-11-09 change that moved the chatbot (`/chat/*`, see
[`CHATBOT.md`](CHATBOT.md)) from Claude Haiku 4.5 to **Amazon Nova Pro**, and what was
done in AWS and on the server. Original one-time setup: [`BEDROCK_SETUP.md`](BEDROCK_SETUP.md).

## Why

Every Claude model on Bedrock was refused for account `631069968633` in `ap-south-1`:

| Refusal | Models |
|---|---|
| `INVALID_PAYMENT_INSTRUMENT` (AWS Marketplace billing never completed) | Haiku 4.5 (`in.`/`global.`), Haiku 3, Sonnet 4.5 / 3.7 / 3.5 / 3, Opus 4.5 |
| "not available for this account" | Sonnet 5 / 5.5, Opus 5, Haiku 5.5, Fable 5.1 |

AWS Support reviewed the account standing but declined expanded Haiku 4.5 access.
Amazon's own Nova models are not billed through Marketplace and work.

## What was tested

`Converse` calls in `ap-south-1` (tiny prompts): **Nova Pro, Nova Lite, Nova Micro and
Nova 2 Lite all respond**, and pass through the existing guardrail
(`8y32pjfflzq6` v1, which correctly blocks off-topic prompts). Nova Pro with the real
planner prompt returned valid plan JSON for three sample questions. One generated SQL
had an invalid `ORDER BY` on an aggregate; the pipeline's SQL retry path is meant to
absorb this. Not yet verified: SQL execution and the answer step on live data.

## What changed

1. **Model** - `CHAT_MODEL_ID` default is now `apac.amazon.nova-pro-v1:0`
   (`app/core/config.py`, `.env.example`). Swapping models is config-only (Converse API).
2. **IAM** - role `brokersync-ec2-bedrock`, policy `bedrock-chat`: added
   `arn:aws:bedrock:*::foundation-model/amazon.nova-*` and
   `arn:aws:bedrock:*:631069968633:inference-profile/*amazon.nova-*` to
   `InvokeChatModels` (also in `scripts/bedrock/bedrock-chat-policy.json`).
3. **Server** (EC2 `13.206.231.240`) - pulled `main` (be1d933), `pip install -r
   requirements.txt`, appended to `.env`: `CHAT_ENABLED=true`,
   `CHAT_MODEL_ID=apac.amazon.nova-pro-v1:0`, `CHAT_GUARDRAIL_ID=8y32pjfflzq6`,
   `CHAT_GUARDRAIL_VERSION=1` (backup: `.env.bak-prechat`), restarted `brokersync`.
   Verified: `/health` 200, `/chat/ask` without a token 401, and the instance role
   invoking Nova Pro with the guardrail succeeds.

## Switching back to Claude

Once the Marketplace payment instrument is fixed: set `CHAT_MODEL_ID` back to
`in.anthropic.claude-haiku-4-5-20251001-v1:0` in the server `.env` and restart
`brokersync`. The planner prompts were tuned on Haiku, so re-check SQL quality. To use
a cheaper Nova tier, `apac.amazon.nova-lite-v1:0` also works.
