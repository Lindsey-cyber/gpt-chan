# GPT娘 · 白龙姬

Private Telegram character bot: Python 3.12, aiogram, OpenAI Responses + Images APIs, SQLite. Run exactly one polling process with persistent storage at `/app/data`.

## Setup

Copy `.env.example` to `.env`, supply secrets using Railway variables, and set `ALLOWED_USER_IDS` to the two numeric IDs. Never commit secrets. `TEXT_MODEL` and `IMAGE_MODEL` are preserved; defaults remain `gpt-4.1-mini` and `gpt-image-2.5-sunburst`. Account access must be verified separately; no automatic model substitutions.

`python -m pip install -r requirements.txt`, then `python -m app.main`. Docker Compose persists `/app/data`. Railway volume ownership is fixed at container start and the worker runs as `bot`.

## Chat and images

Natural requests, contextual revisions (精致一点), and continuations (继续出图) use a strict `generate_image` function call from the configured text model. The application validates the arguments, calls the Images API, caches PNG bytes, then sends a Telegram photo. It never presents the tool prompt as the generated result. `/draw <requirements>` is an explicit entry point through the same planner.

The fixed character's supplied three-view image is `assets/gptchan_reference.png`. Self portraits use Images edits with the reference. The model must preserve the face, hair, horns, wings, tail, dress and accessories, but pixel-perfect identity is not guaranteed. To replace the shared visual reference, send a Telegram **photo** with caption `/setref`. It is validated and atomically installed at `/app/data/gptchan_reference.jpg`; failed downloads preserve the previous reference. `/ref` reports availability. The reference is intentionally shared; conversation, memory, mood, tasks and generated image caches are isolated by numeric user ID.

`/new` clears only the sender's conversation, mood and image task; long-term memories stay. `/memories` lists memories. `/forget <id>` deletes only the sender's memory with that ID; `/forget all` clears their memories.

## Reliability and costs

All authorized updates for a user are serialized, including `/new`; different users have independent locks. Image generation is globally limited to one concurrent request. Persistent limits default to 12 attempts/user/hour and a 15-second cooldown. Attempts, including failures, consume the local limit. One request produces one 1024×1024 image at explicit `high` quality. Change `IMAGE_QUALITY` to low/medium/high if wanted. The image default was upgraded from `gpt-image-1-mini` to `gpt-image-2.5-sunburst` with user authorization; no automatic fallback or substitutions occur. Production `TEXT_MODEL=gpt-5.2` remains unchanged.

Image calls have a 240-second timeout and **no automatic retries** because a timeout can hide a completed paid generation. Text calls have two bounded SDK retries. Rate/quota and access errors get concise messages. Successful images are cached per user before sending; if Telegram fails, “图片呢” uses `image_status` to resend cached bytes without regenerating. Restart marks unfinished generation as interrupted and never silently replays it. Only the latest generated PNG per user is retained.

Memory extraction runs every fourth text exchange or on explicit memory cues, using a durable counter independent of rolling history. Logs exclude raw exception bodies and private conversation content; image request IDs and token usage are logged for billing investigation. Text Responses use `store=False`.

Before the first storage upgrade, an existing SQLite database is backed up with SQLite's online backup API to `gptchan.sqlite3.pre-reliability.bak` on the same volume. Schema updates are additive (`CREATE TABLE IF NOT EXISTS`); no existing history/memory schema or volume is replaced. This local snapshot does not replace off-platform backups. Do not delete the volume or run multiple replicas.

## Verification and deployment

Run `python -m compileall -q app` and `python -m pytest -q`. Tests use real aiogram dispatch with mocked Telegram transport and mocked OpenAI, testing isolation, command/photo routing, image states, cache resend, reference atomicity, API contracts, migration retention, concurrency and startup. GitHub Actions repeats both checks. Docker also runs both before an image can be deployed, so a failed test prevents deployment even if Railway's GitHub check waiting is disabled.

For an authorized real-API deployment probe, set `RUN_STARTUP_PROBE=true`. It runs once per image-model/quality pair on a persistent volume, guarded by a hashed `api-probe-v2-*.json` marker, tests text output and the three image request/refinement/continuation decisions, and performs **one paid reference-image edit**. Result bytes go to `api-probe-portrait.png`. It never sends fabricated incoming Telegram updates or unsolicited chat messages. The marker records attempted/passed/failed; restarts do not incur more probe cost. Logs explicitly distinguish API verification from untested Telegram photo delivery.

A Railway `SUCCESS` label only proves the deployment lifecycle completed. Check `bot_ready`, `Run polling`, and subsequent real `Update ... is handled` logs; verify a real Telegram user receives text and a photo. API probes and mocked tests do not establish real Telegram delivery or subjective visual consistency.

## Character continuity checks

The persona separates fixed fictional character/world facts from real user facts and real tool results. Ordinary questions about dragons, home or hobbies stay in the current character conversation. Explicit real-identity questions remain honest; technical answers and tool outcomes keep their accuracy requirements. Existing user memories and history are not rewritten when personality changes.

`RUN_PERSONA_PROBE=true` enables one synthetic 21-turn real-text-model check per persona/text-model revision. It deliberately includes an earlier OOC assistant response, then tests conversational recovery, world continuity, ordinary banter, repeated cat metaphors, a request for cuter speech, stable dragon identity, arithmetic, explicit real identity, image intent/refinement and image status. It reads no user database and performs no image generation or Telegram sends. A hashed `persona-probe-*.json` report stores synthetic samples on the volume; logs label those samples explicitly. Structural checks report meta explanation, excessive verbosity/ellipses, basic correctness and routing issues; subjective voice quality still requires human review. Exceptions/timeouts do not prevent normal polling. Disable the flag after the deployment check.

Character-writing references (inspiration for contrasts and concrete preferences, not copied dialogue or borrowed identity): the official character profiles for [Kaguya-sama](https://kaguyasama-anime.com/character/), [Fern](https://frieren-anime.jp/character/chara_group1/1-5/) and [Holo](https://spice-and-wolf.com/).

For the documented `gpt-5.2` model and its dated snapshots, chat requests use `text.verbosity=low` alongside explicit short casual-reply guidance. Other model families receive no unsupported verbosity field. This is a style hint, not an output truncation rule; explicit requests for detailed technical answers remain supported. The persona probe uses simulated successful image delivery in its history consistently with its `sent` task state, without actually generating or sending an image.

Voice v6 favors context-driven, expressive Chinese chat over a fixed sentence/punctuation template. Requests for cuter speech should be answered through the next in-character reaction rather than an announcement about adjusting style. Playful animal metaphors do not change canonical identity. The synthetic probe now includes the reported dragon/cat/style-correction sequence as regression cases.

Voice v7 treats the owner/white-dragon relationship as the default across casual chat and task answers. Generic code/error/tutorial keywords now suggest playful competence rather than switching to a plain work persona. Explicit requests to stop affection use a temporary plain state; requests to check carefully preserve affection while increasing attentiveness. Legacy focused states remain readable and are interpreted as care with the answer, not loss of character. The live probe follows the same mood transition function, includes familiar identity teasing and an actual NameError explanation, and checks explicit plain-output requests separately. Image completion captions retain the same owner-facing voice after real delivery succeeds.

The requested Grok-like inspiration concerns wit, initiative and irreverent friendly banter ([official description](https://help.x.com/en/using-x/about-grok)); the service continues to use its configured OpenAI models, with no xAI account, provider or credentials added.

Voice v7.1 reinforces owner-facing affection for every non-plain mood, including careful task answers. A permission to use punctuation is not a request for a global cold punctuation style. The synthetic check distinguishes that permission from general style requests, checks affectionate arithmetic separately from explicit plain arithmetic, and bounds the simple NameError scenario to a concise useful fix. Casual structural checks now allow two short paragraphs and occasional ellipses, in line with the user's updated expressive preference, while flagging long/three-paragraph exposition or ellipses on most casual turns. These checks remain review aids, not a guarantee of subjective personality quality.


### 两套人格切换

默认保留原版白龙姬，不会自动改名。私聊发送：

- `/persona` 查看当前人格和切换方法。
- `/persona 白龙姬` 或 `/persona bailongji`：原版天才白龙姬。
- `/persona 小白` 或 `/persona xiaobai`：用户提供的Grok稿小龙宠；`grok`也是别名，不更换底层模型。

选择按授权用户持久保存，重启仍在；两套情绪分别保存，聊天和长期记忆沿用当前用户的记录，切换不清空。`/new`仅清当前用户的短期对话及情绪，保留人格选择与长期记忆。图片生成、模型和固定立绘共用。

主动消息独立控制，默认关闭，切人格不会擅自开关：`/proactive on` 开启，`/proactive off` 关闭，`/proactive` 看状态；`/timezone Asia/Shanghai` 改自己的时区（默认Asia/Tokyo）。只绑定授权用户实际发来的私聊，不猜测chat_id。每天最多4条（配置范围2–5），通常间隔至少两小时；23–8点安静，深夜仅刚收到消息时可提醒休息，不读取在线状态。SQLite先登记发送尝试再调用Telegram，重启不重播不确定结果；被屏蔽自动停发。主动消息仅文本，每次最多220输出token，不调用生图。
