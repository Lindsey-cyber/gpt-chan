import asyncio
import logging
import time
from contextlib import suppress
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pathlib import Path
from uuid import uuid4

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, FSInputFile, Message

from app.ai import Brain, ImageRequest, ImageStatus
from app.api_errors import error_details, error_message
from app.config import Settings
from app.companion import Companion, CompanionStore, local_day
from app.mood import next_mood
from app.personas import PROFILES, resolve_profile, profile_prompt
from app.captions import image_caption
from app.tool_errors import ToolProtocolError
from app.image_errors import unknown_outcome, UNKNOWN_OUTCOME_MESSAGE
from app.image_state import ImageState, SENDABLE_STATES, RECOVERABLE_STATES
from app.image_routing import ImageBackend, image_backend
from app.telegram_text import answer_text
from app.storage import Storage

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
PORTRAIT = ('GPT娘·白龙姬，同一位成年白龙少女。以参考三视图为角色身份依据：银白色长发、浅紫蓝眼睛、'
            '白色弯曲龙角、淡紫色膜翼、白鳞长尾、白银淡紫的龙纹长裙、玫瑰发饰与服装配饰。'
            '保持脸型、发型、角、翅膀、尾巴和服装一致，参考图优先于文字中的外貌冲突。'
            '画一幅完整插画，不要三视图、文字或水印。具体场景与构图：')

def report_error(label, exc):
    # Only allowlisted structured metadata: never error bodies or messages.
    detail = error_details(exc)
    log.error('%s type=%s status=%s request_id=%s code=%s api_type=%s param=%s moderation_stage=%s local_reason=%s',
        label,type(exc).__name__,getattr(exc,'status_code',None),getattr(exc,'request_id',None),
        detail['code'],detail['api_type'],detail['param'],detail['moderation_stage'],detail['local_reason'])

class UserSerial(BaseMiddleware):
    def __init__(self, settings, companion=None):
        self.settings, self.locks = settings, {}
        self.companion = companion

    async def __call__(self, handler, event, data):
        if event.from_user and event.from_user.id in self.settings.allowed_ids:
            async with self.locks.setdefault(event.from_user.id, asyncio.Lock()):
                if self.companion and event.chat.type == 'private':
                    text = event.text or ''
                    affection = any(w in text for w in ('摸头','摸摸','抱抱','你好棒','你真棒','可爱','喜欢你','喂你','给你零食')) and not text.startswith('/')
                    await self.companion.activity(event.from_user.id,event.chat.id,time.time(),
                        self.settings.proactive_timezone,False,affection)
                return await handler(event, data)
        return await handler(event, data)

def reference_path(settings):
    custom = Path(settings.database_path).parent / 'gptchan_reference.jpg'
    return custom if custom.is_file() and custom.stat().st_size else ROOT / 'assets/gptchan_reference.png'

async def save_reference(bot, photo, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f'.reference-{uuid4().hex}.jpg')
    try:
        await bot.download(photo, destination=temp)
        from PIL import Image
        with Image.open(temp) as img:
            img.verify()
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)

def build_router(settings, store, brain, serial=None, companion=None):
    router = Router()
    router.message.outer_middleware(serial or UserSerial(settings, companion))
    image_slots = asyncio.Semaphore(1)

    def permitted(message):
        return bool(message.from_user and message.from_user.id in settings.allowed_ids and message.chat.type == 'private')

    @router.message(Command('id'))
    async def identity(message):
        if message.from_user and message.chat.type == 'private':
            await message.answer(f'你的 Telegram ID: {message.from_user.id}')

    @router.message(CommandStart())
    async def start(message):
        if permitted(message):
            key = await store.selected_persona(message.from_user.id)
            await message.answer('主人！小白在这里呀～先摸摸角嘛♡' if key == 'xiaobai' else '主人，白龙姬在这里呀～今天想一起做什么？')

    @router.message(Command('persona'))
    async def persona_settings(message):
        if not permitted(message): return
        uid = message.from_user.id
        arg = (message.text or '').split(maxsplit=1)
        if len(arg) == 1:
            key = await store.selected_persona(uid)
            await message.answer(f"当前人格：{PROFILES[key][0]}。\n/persona bailongji — 原来的天才白龙姬\n/persona xiaobai — Grok稿的小龙宠小白\n也可以用 /persona 白龙姬 或 /persona 小白。聊天、记忆和参考立绘都会保留。")
            return
        key = resolve_profile(arg[1])
        if not key:
            await message.answer('主人，选 /persona 白龙姬 或 /persona 小白 就好啦。')
            return
        await store.select_persona(uid,key)
        await message.answer('主人！小白来啦♡ 先摸摸角嘛，怎么可以只换完就走！' if key == 'xiaobai' else '主人，白龙姬回来啦！这回也要让你见识本天才的本事～')

    @router.message(Command('proactive'))
    async def proactive_settings(message):
        if not permitted(message) or companion is None: return
        uid = message.from_user.id
        arg = (message.text or '').split(maxsplit=1)
        action = arg[1].strip().lower() if len(arg) == 2 else 'status'
        if action in ('on','off'):
            await companion.configure(uid,enabled=action == 'on')
            await message.answer('好嘛，我会自己来找主人玩♡' if action == 'on' else '知道啦，我乖乖等主人来找。主动消息关好啦。')
        else:
            user = next(u for u in await companion.users() if u['user_id'] == uid)
            await message.answer(f"主动找主人：{'开着' if user['enabled'] else '关着'}；时区 {user['timezone']}；每天最多{settings.proactive_daily_limit}条，通常间隔至少两小时。\n夜里23点到早上8点安静，只有深夜刚收到你的消息才可能提醒休息。\n/proactive on 或 /proactive off；/timezone Asia/Shanghai 可以改时区。")

    @router.message(Command('timezone'))
    async def timezone_settings(message):
        if not permitted(message) or companion is None: return
        arg = (message.text or '').split(maxsplit=1)
        try:
            if len(arg) != 2: raise ValueError()
            ZoneInfo(arg[1].strip())
        except (ValueError, ZoneInfoNotFoundError):
            await message.answer('主人，给我一个时区名字嘛，例如 /timezone Asia/Shanghai 或 /timezone Asia/Tokyo。')
            return
        await companion.configure(message.from_user.id,timezone=arg[1].strip())
        await message.answer('记好啦，之后按主人这个时区来找你♡')

    @router.message(Command('new'))
    async def new(message):
        if permitted(message):
            await store.clear_history(message.from_user.id)
            await message.answer('新的对话开始啦，长期记忆还在。')

    @router.message(Command('memories'))
    async def memories(message):
        if not permitted(message): return
        rows = await store.memories(message.from_user.id, settings.memory_limit)
        await answer_text(message, '我记得这些：\n'+ ('\n'.join(f'{i}. {f}' for i, f in rows) or '（暂无）'))

    @router.message(Command('forget'))
    async def forget(message):
        if not permitted(message): return
        arg = (message.text or '').split(maxsplit=1)
        if len(arg) != 2 or (arg[1] != 'all' and not arg[1].isdigit()):
            await message.answer('用 /forget 记忆编号，或 /forget all 清空。')
            return
        await store.forget(message.from_user.id, None if arg[1] == 'all' else int(arg[1]))
        await message.answer('已经按你的要求删除了。')

    @router.message(F.photo, Command('setref'))
    async def reference_photo(message):
        if not permitted(message): return
        try:
            await save_reference(message.bot, message.photo[-1], Path(settings.database_path).parent / 'gptchan_reference.jpg')
            await message.answer('立绘收好啦，以后画我自己会参考它。')
        except Exception as exc:
            report_error('reference_save_failed', exc)
            await message.answer('这次没存好，之前的立绘还在，可以再发一次。')

    @router.message(Command('setref'))
    async def reference_help(message):
        if permitted(message):
            await message.answer('把正式立绘作为照片发送，并在照片说明中写 /setref。')

    @router.message(Command('ref'))
    async def reference_status(message):
        if permitted(message):
            await message.answer('正式立绘已保存。' if reference_path(settings).is_file() else '暂时没有可用立绘。')

    async def cache_path(uid):
        task_id = await store.image_task_id(uid)
        suffix = f'_{task_id}' if task_id else ''
        return Path(settings.database_path).parent / f'image_{uid}{suffix}.png'

    async def recover_cached_task(uid, task):
        # Only a file associated with this task can prove generation completed.
        if (task and task[1] in RECOVERABLE_STATES and await store.image_task_id(uid)
                and (await cache_path(uid)).is_file()):
            return (task[0], ImageState.GENERATED)
        return task

    async def send_cached(message, task, caption=None):
        uid = message.from_user.id
        task = await recover_cached_task(uid, task)
        cached = await cache_path(uid)
        if task and task[1] in SENDABLE_STATES and cached.is_file():
            try:
                await message.answer_photo(FSInputFile(cached),caption=caption or await image_caption(store,uid,resend=True))
            except Exception as exc:
                report_error('image_delivery_failed',exc)
                await store.set_image_task(uid,task[0],ImageState.DELIVERY_FAILED)
                await message.answer('图片已生成，但发送失败。说「图片呢」可以重发，不会重新画。')
                return False
            try:
                await store.set_image_task(uid,task[0],ImageState.SENT)
            except Exception as exc:
                # Telegram acknowledged delivery; persistence failure is not a send failure.
                report_error('image_state_save_failed',exc)
            if caption is None:
                try:
                    await store.add_assistant(uid,'[上一张图片已从缓存重新发送；没有重新生成]',settings.max_context_messages)
                except Exception as exc:
                    report_error('image_history_save_failed',exc)
            return True
        await message.answer('主人，目前没有可重发的缓存图片；这次没有重新画。')
        return False

    async def send_image(message, request, original):
        uid = message.from_user.id
        backend = image_backend(request.nsfw)
        if backend == ImageBackend.NOVELAI and not settings.novelai_token.strip().startswith('pst-'):
            await message.answer('主人，这次需要 NovelAI 画板，但尚未配置有效凭据。绘图请求没有发送，也没有切换到其他后端。')
            return
        # NSFW 走 NovelAI 时不用 OpenAI 的中文 PORTRAIT 前缀（NovelAI 用英文标签）
        if backend == ImageBackend.NOVELAI:
            prompt = request.prompt
        else:
            prompt = (PORTRAIT if request.self_portrait else '') + request.prompt

        if not await store.reserve_image(uid, settings.image_hourly_limit, settings.image_cooldown_seconds):
            await message.answer('画得有点密啦，稍等一会儿再来。')
            return

        await store.set_image_task(uid, prompt, ImageState.GENERATING)
        generation_started = False
        try:
            await store.set_image_route(uid, request.nsfw, request.self_portrait)
            async with image_slots:
                await message.bot.send_chat_action(message.chat.id, 'upload_photo')
                if backend == ImageBackend.NOVELAI:
                    generation_started = True
                    data = await brain.image(prompt, nsfw=True, self_portrait=request.self_portrait)
                else:
                    ref = str(reference_path(settings)) if request.self_portrait else None
                    generation_started = True
                    data = await brain.image(prompt, ref, quality=settings.image_quality, nsfw=False)

                cached = await cache_path(uid)
                temp = cached.with_suffix('.tmp')
                temp.write_bytes(data)
                temp.replace(cached)
                # Old task files cannot be mistaken for a new generation.
                for previous in cached.parent.glob(f'image_{uid}_*.png'):
                    if previous != cached:
                        try:
                            previous.unlink(missing_ok=True)
                        except OSError as exc:
                            report_error('image_cache_cleanup_failed', exc)

        except asyncio.CancelledError:
            await store.set_image_task(uid, prompt, ImageState.UNKNOWN if generation_started else ImageState.INTERRUPTED)
            raise
        except Exception as exc:
            uncertain = generation_started and unknown_outcome(exc)
            await store.set_image_task(uid, prompt, ImageState.UNKNOWN if uncertain else ImageState.FAILED)
            report_error('image_generation_failed', exc)
            msg = error_message(exc, image=True)
            if uncertain:
                msg = UNKNOWN_OUTCOME_MESSAGE
            elif 'NovelAI' in type(exc).__name__ or 'novelai' in str(exc).lower() or 'Anlas' in str(exc):
                msg = '主人……这次亲密的画没画成（额度或接口问题）。小白没有自动重试，你想再试一次就直接说。'
            await message.answer(msg)
            return

        # Generation, delivery, and conversation persistence have separate outcomes.
        try:
            await store.set_image_task(uid,prompt,ImageState.GENERATED)
            try:
                caption = await image_caption(store,uid)
            except Exception as exc:
                report_error('image_caption_failed',exc)
                caption = '主人，这张给你♡ 我在旁边陪你一起看。'
            delivered = await send_cached(message,(prompt,ImageState.GENERATED),caption)
        except Exception as exc:
            report_error('image_postprocess_failed',exc)
            await message.answer('主人，图片已经画好并存着，但发送记录出了点问题。这次没有重新画。')
            return
        status_note = '已生成并发送图片' if delivered else '图片已生成，但发送失败，尚未送达'
        context_note = f'[{status_note}｜nsfw={request.nsfw}｜self_portrait={request.self_portrait}｜绘图要求摘要（非图片实看描述）：{request.prompt[:400]}]'
        try:
            await store.add_exchange(uid,original,context_note,settings.max_context_messages)
        except Exception as exc:
            report_error('image_history_save_failed',exc)
            # Do not overwrite a successful delivery as a failed generation.
            if delivered:
                await message.answer('主人，图片已经发给你啦，不过这次对话记录没存好；图片不用重画。')

    @router.message(Command('draw'))
    async def draw(message):
        if not permitted(message): return
        prompt = (message.text or '').partition(' ')[2].strip()
        if not prompt or len(prompt) > 3000:
            await message.answer('用 /draw 加上绘图要求，最多3000字。')
            return
        # Explicit command still uses the model to resolve identity and context.
        await respond(message, '请现在生成一张图片：' + prompt)

    async def respond(message, text):
        uid = message.from_user.id
        try:
            history = await store.history(uid, settings.max_context_messages, settings.max_context_chars)
            memories = await store.memories(uid, settings.memory_limit)
            task = await recover_cached_task(uid, await store.image_task(uid))
            route = await store.image_route(uid)
            persona = await store.selected_persona(uid)
            previous, remaining = await store.character_state(uid,persona)
            mood, remaining = next_mood(text, previous, remaining)
            await message.bot.send_chat_action(message.chat.id, 'typing')
            greeting = False
            if companion and persona == 'xiaobai':
                user = next(u for u in await companion.users() if u['user_id'] == uid)
                greeting = await companion.greet(uid,local_day(time.time(),user['timezone']))
            answer = await brain.chat(history, memories, text, mood, image_task=task, daily_greeting=greeting and persona == 'xiaobai',persona=persona,image_route=route)
            if isinstance(answer,ImageStatus):
                if task and task[1] in SENDABLE_STATES:
                    await send_cached(message,task)
                    return
                if task and task[1] in (ImageState.UNKNOWN, ImageState.INTERRUPTED):
                    await message.answer(UNKNOWN_OUTCOME_MESSAGE)
                    return
                # One bounded re-evaluation with the status tool disabled. This can
                # answer ordinary chat or resolve an actual revision into a new image.
                answer = await brain.chat(history,memories,text,mood,image_task=task,persona=persona,status_checked=True,image_route=route)
                if isinstance(answer,ImageStatus):
                    raise ToolProtocolError('repeated_status')
            if isinstance(answer,ImageRequest):
                await store.set_character_state(uid,mood,remaining,persona)
                await send_image(message,answer,text)
                return
            await answer_text(message, answer)
            await store.set_character_state(uid,mood,remaining,persona)
            await store.add_exchange(uid, text, answer, settings.max_context_messages)
            if await store.should_extract(uid) or any(w in text for w in ('记住', '我喜欢', '我讨厌', '以后请')):
                try:
                    facts = await brain.extract_memory(text, answer, memories)
                    if facts: await store.save_memories(uid, facts, settings.memory_limit)
                except Exception as exc:
                    report_error('memory_extract_failed', exc)
        except Exception as exc:
            report_error('chat_failed', exc)
            await message.answer(error_message(exc))

    @router.message(F.text)
    async def chat(message):
        if not permitted(message): return
        text = message.text.strip()
        if text.startswith('/'): return
        if len(text) > 8000:
            await message.answer('这段太长啦，分几次发给我吧。')
            return
        await respond(message, text)
    return router

async def main():
    logging.basicConfig(level=logging.INFO)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    settings = Settings()
    if not settings.allowed_ids: raise RuntimeError('ALLOWED_USER_IDS must not be empty')
    store = Storage(settings.database_path)
    await store.init()
    companion = CompanionStore(settings.database_path)
    await companion.init()
    brain = Brain(
                settings.openai_api_key,
                settings.text_model,
                settings.image_model,
                profile_prompt('bailongji'),
                novelai_token=settings.novelai_token,
            )
    bot = Bot(settings.telegram_bot_token)
    dp = Dispatcher()
    serial = UserSerial(settings,companion)
    dp.include_router(build_router(settings,store,brain,serial,companion))
    proactive_task = None
    try:
        await bot.get_me()
        await bot.delete_webhook(drop_pending_updates=False)
        log.info('bot_ready allowed_users=%d text_model=%s image_model=%s reference=%s',
                 len(settings.allowed_ids), settings.text_model, settings.image_model, reference_path(settings).is_file())
        if settings.run_startup_probe:
            from app.probe import startup_probe
            await startup_probe(brain, Path(settings.database_path).parent, reference_path(settings), quality=settings.image_quality)
        if settings.run_persona_probe:
            from app.persona_probe import persona_probe
            try:
                await asyncio.wait_for(persona_probe(brain, Path(settings.database_path).parent), timeout=120)
            except asyncio.TimeoutError:
                log.warning('persona_probe_timeout; polling will start without repeating the probe')
            from app.profile_probe import profile_probe
            try:
                await asyncio.wait_for(profile_probe(brain,Path(settings.database_path).parent),timeout=90)
            except asyncio.TimeoutError:
                log.warning('profile_probe_timeout; polling will start')
        if settings.proactive_enabled:
            proactive_task = asyncio.create_task(Companion(settings,companion,store,brain,bot,serial.locks).run())
        await dp.start_polling(bot)
    finally:
        if proactive_task:
            proactive_task.cancel()
            with suppress(asyncio.CancelledError):
                await proactive_task
        await brain.client.close()
        await bot.session.close()

if __name__ == '__main__':
    asyncio.run(main())
