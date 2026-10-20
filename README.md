# GPT娘 — Telegram 白龙娘 Bot

Private, lightweight Telegram character bot using Python, aiogram, OpenAI Responses API, SQLite, and Docker.

## Capabilities

- Persona in `personas/gptchan.md`: white dragon girl, blue eyes, blue/white/black/silver palette, playful tsundere who naturally calls the user 主人
- Telegram private-chat **numeric-ID allowlist**, default deny (the public `/id` route only returns the sender's own ID)
- Conservative **automatic** memory extraction, isolated per user; inspect with `/memories`, erase with `/forget <id>` or `/forget all`
- Rolling history (up to 24 messages / 32000 characters), stored in SQLite and cleared with `/new`
- Image generation by natural-language requests such as `帮我画一张白龙娘头像`, and explicit fallback `/draw <prompt>`
- Docker Compose and named volume for persistent data; no webhook or public port required

## Setup

1. Copy `.env.example` to `.env`. Set `TELEGRAM_BOT_TOKEN` and `OPENAI_API_KEY` from your own secret manager; **never commit credentials**.
2. Ask the two intended Telegram accounts (`@allliiita` and `@HammerZhou`) to send `/id`; collect their **numeric** Telegram IDs. Set `ALLOWED_USER_IDS=id1,id2`. Usernames are not used for authorization.
3. Local: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && DATABASE_PATH=./data/gptchan.sqlite3 python -m app.main`
4. Docker: `docker compose up -d --build`. Deploy as exactly **one** long-polling process on a cloud worker/VPS and persist `/app/data`.

## Constraints and roadmap

- Automatic memory is conservative, heuristic, and may miss things; no vector DB, no cross-user memory sharing. Sensitive information should not be recorded. Users can review and remove stored memories. Memory extraction involves additional model requests.
- Image generation consumes paid API credits; image model availability differs per account.
- Current natural-language drawing detection uses simple rule-based intent matching. It may miss some requests or match some unintended prompts. The image model does not automatically inherit chat context.
- The repository is an **initial foundation**, not an end-to-end verified deployment. Use test credentials in your own hosting environment and watch usage.
- Planned: schema migrations, model-powered tool routing, structured memory updates/merging, optional bot bindings for multi-character support, observability, automated dependency checks.
