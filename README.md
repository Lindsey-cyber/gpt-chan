# GPT娘 · 白龙姬

Private Telegram character bot using Python 3.12, aiogram, OpenAI Responses/Images,
an optional NovelAI image backend, and SQLite. Run **one polling process** with
persistent storage at `/app/data`.

## Setup

1. Copy `.env.example` to `.env`, or set the same environment variables in your host.
2. Supply `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, and comma-separated numeric
   `ALLOWED_USER_IDS`. An empty allowlist prevents startup. Keep credentials out of git.
3. Install `python -m pip install -r requirements.txt` and run `python -m app.main`.
   Alternatively, `docker compose up --build -d` persists data in a named volume.

Defaults are `TEXT_MODEL=gpt-4.1-mini` and `IMAGE_MODEL=gpt-image-2.5-sunburst`.
Verify model access with the provider; there are no automatic substitutions.
Existing deployment variables take precedence over these defaults.

`requirements.in` lists direct dependencies. `requirements.txt` locks direct and
transitive versions for Python 3.12 on Linux, the Docker/CI target. Update the input
and lock together in a clean environment, then run the full suite. Pytest remains
in the build dependencies because the Docker build tests before deployment.

The container fixes ownership of the volume directory at startup and runs the
polling process as the non-root `bot` user. Do not remove the volume or scale to
multiple polling replicas.

## Commands and isolation

Only allowlisted private chats can use the bot, except `/id`, which reports the
sender's own Telegram ID. Conversation, memory, mood, persona selection, image
tasks and image caches belong to the numeric user ID.

| Command | Behavior |
| --- | --- |
| `/persona` | Show the current persona and choices. |
| `/persona 白龙姬` or `/persona bailongji` | Select the original persona. |
| `/persona 小白` or `/persona xiaobai` | Select the alternative persona; `grok` is an alias, not a provider change. |
| `/new` | Clear short-term conversation, mood and image task; keep long-term memory and persona selection. |
| `/memories` | List the sender's memories in bounded Telegram messages. |
| `/forget <id>` or `/forget all` | Delete the sender's selected memory or all saved memories. |
| `/draw <requirements>` | Request an image through the normal intent planner. |
| `/setref` in a photo caption | Replace the intentionally shared character reference. |
| `/ref` | Check reference availability. |
| `/proactive on`, `/proactive off`, `/proactive` | Control proactive messages. New users start disabled. |
| `/timezone Asia/Shanghai` | Change the sender's proactive timezone. Default: `Asia/Tokyo`. |

The bundled reference is `assets/gptchan_reference.png`; a successfully downloaded
and verified custom reference is atomically installed as
`/app/data/gptchan_reference.jpg`. A failed update preserves the previous file.
Both personas and all authorized users intentionally share that reference.
Character-writing history is in [docs/character-history.md](docs/character-history.md).

## Images, limits and recovery

Natural requests, contextual revisions and continuations use a validated
`generate_image` function call. Progress questions use `image_status`. A prompt,
link or roleplayed completion is never treated as an actual image result.

OpenAI self portraits use reference-image edits; other subjects use generation.
The OpenAI request is one 1024×1024 PNG with `IMAGE_QUALITY=high` by default.
The optional NovelAI backend uses its own 832×1216 dimensions and model settings.
It requires `NOVELAI_TOKEN`; fixed character tags are added only for self portraits.
Backend selection does not automatically add a stronger content-rating prompt.

Authorized messages are serialized per user. Image generation has one shared
slot. Persistent attempt limits default to 12 per user per hour, with a 15-second
cooldown; failed attempts also consume the local limit.

NovelAI responses are streamed with these limits:

- 8 MiB downloaded archive; 8 archive entries; 32 MiB declared total expansion.
- One PNG, at most 16 MiB, with a ZIP compression ratio no greater than 200.
- At most 4,194,304 pixels and 4096 pixels per dimension; animated PNGs are rejected.

The archive member read is independently bounded. PNG container integrity and
actual pixel decoding are checked before caching or sending. Archive parsing and
image validation run off the event loop. OpenAI PNG results receive the same
byte/pixel/integrity checks.

NovelAI has a **180-second total deadline** covering the provider request, complete
download and validation, in addition to HTTPX phase timeouts. Queue waiting occurs
before this deadline. A timed-out validation worker may finish its bounded local
work in the background; it cannot cache or send a result. OpenAI image requests
retain their 240-second SDK timeout. Neither image backend automatically retries.
Text calls allow two SDK retries; error messages say when further retries stop.

Image states are defined in `app/image_state.py`. Each new task receives an ID;
its PNG is atomically cached under that ID before delivery. If the process exits
or saving the `generated` state fails after caching, a status query can recover
the matching file without generating again. A previous task's file cannot stand
in for a new task. Old databases and legacy delivery-failure caches remain readable.

Confirmed delivery becomes `sent`; a delivery failure remains resendable. Timeout,
connection interruption or an ambiguous server error becomes `unknown`, since
the provider may already have generated or charged. Startup marks unfinished tasks
`interrupted`. Status queries never replay those uncertain tasks; check provider
account records before explicitly requesting another paid generation.

## Memory and proactive messages

Memory extraction on text replies uses a durable exchange counter (every fourth
saved exchange) or explicit memory cues, independent of rolling history. It asks only for
stable, non-sensitive preferences or explicit long-term plans. `/forget` removes
saved facts; it does not erase matching text from the current conversation or old
backups. Use `/new` to clear short-term conversation too.

`MAX_CONTEXT_MESSAGES` accepts 2–200, `MAX_CONTEXT_CHARS` accepts 1,000–200,000,
and `MEMORY_LIMIT` accepts 1–100. Invalid values fail settings validation.

Proactive messages bind only to actual incoming allowlisted private chats. They
are opt-in per user and additionally controlled by `PROACTIVE_ENABLED`. The daily
cap defaults to four attempts, normally at least two hours apart. Quiet hours are
23:00–08:00, except a brief late-night rest reminder after a recent inbound message.
The scheduler checks fresh time after waiting for a user lock and again before
sending; expired events are not sent or replayed. It does not inspect online status,
send images or invoke image tools. Blocking the bot disables proactive messages.

## Verification and operations

Run:

```sh
python -m compileall -q app
python -m pytest -q
```

Tests use aiogram dispatch, SQLite and mocked provider/Telegram transports. They
cover isolation, image routing, response limits, malformed PNGs, total deadlines,
uncertain outcomes, cache recovery, old-schema migration, pagination and scheduler
boundaries. CI and the Docker build run the suite. Passing mocks does not establish
real provider access, Telegram delivery or subjective character/image quality.

`RUN_STARTUP_PROBE=true` opts into one marked API probe per image-model/quality pair,
including **one paid reference edit**. `RUN_PERSONA_PROBE=true` enables marked
synthetic text/persona probes. These do not read user conversations or send Telegram
messages. Persisted attempted/passed/failed markers prevent automatic replays;
disable probe flags after checking the deployment.

Logs filter raw exception bodies and private conversation content. Image request
IDs and usage are logged for billing investigation. Responses use `store=False`.
Check `bot_ready`, polling startup and real user delivery when verifying deployment.

Storage upgrades are additive. Existing databases receive one-time local SQLite
backup snapshots before initialization; these may contain older conversations or
memories. They are not rotating or off-platform backups. Protect and back up the
volume separately.
