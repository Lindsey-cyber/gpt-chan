import asyncio
import logging
from pathlib import Path
from uuid import uuid4

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, FSInputFile, Message
from openai import APIStatusError, APITimeoutError, RateLimitError

from app.ai import Brain, ImageRequest, ImageStatus
from app.config import Settings
from app.mood import next_mood
from app.storage import Storage

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
PORTRAIT = ('GPT娘·白龙姬，同一位成年白龙少女。以参考三视图为角色身份依据：银白色长发、浅紫蓝眼睛、'
            '白色弯曲龙角、淡紫色膜翼、白鳞长尾、白银淡紫的龙纹长裙、玫瑰发饰与服装配饰。'
            '保持脸型、发型、角、翅膀、尾巴和服装一致，参考图优先于文字中的外貌冲突。'
            '画一幅完整插画，不要三视图、文字或水印。具体场景与构图：')

def report_error(label, exc):
    # Do not log exception bodies, requests, tokens, prompts, or private messages.
    log.error('%s type=%s status=%s request_id=%s', label, type(exc).__name__,
              getattr(exc, 'status_code', None), getattr(exc, 'request_id', None))

def error_message(exc):
    if isinstance(exc, RateLimitError):
        return '请求额度或速率暂时受限，稍后再试。'
    if isinstance(exc, APITimeoutError):
        return '这次等待超时了，结果暂时无法确认。我没有自动重画；需要的话请明确说再试一次。'
    if isinstance(exc, APIStatusError) and exc.status_code in (400, 401, 403, 404):
        return '这次请求被接口拒绝了，需要检查模型权限、账户配置或绘图要求。'
    return '这次没能完成，稍后再试一次吧。'

class UserSerial(BaseMiddleware):
    def __init__(self, settings):
        self.settings, self.locks = settings, {}

    async def __call__(self, handler, event, data):
        if event.from_user and event.from_user.id in self.settings.allowed_ids:
            async with self.locks.setdefault(event.from_user.id, asyncio.Lock()):
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

def build_router(settings, store, brain):
    router = Router()
    router.message.outer_middleware(UserSerial(settings))
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
            await message.answer('在呀，今天想聊什么？')

    @router.message(Command('new'))
    async def new(message):
        if permitted(message):
            await store.clear_history(message.from_user.id)
            await message.answer('新的对话开始啦，长期记忆还在。')

    @router.message(Command('memories'))
    async def memories(message):
        if not permitted(message): return
        rows = await store.memories(message.from_user.id, settings.memory_limit)
        await message.answer('我记得这些：\n'+ ('\n'.join(f'{i}. {f}' for i, f in rows) or '（暂无）'))

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

    async def send_cached(message, task):
        uid = message.from_user.id
        cached = Path(settings.database_path).parent / f'image_{uid}.png'
        if task and task[1] in ('generated', 'delivery_failed') and cached.is_file():
            try:
                await message.answer_photo(FSInputFile(cached), caption='画好啦。')
                await store.set_image_task(uid, task[0], 'sent')
            except Exception as exc:
                report_error('image_delivery_failed', exc)
                await store.set_image_task(uid, task[0], 'delivery_failed')
                await message.answer('图片已生成，但发送失败。说「图片呢」可以重发，不会重新画。')
            return
        status = task[1] if task else None
        await message.answer({'sent': '上一张已经发送啦。要再画一张可以直接告诉我。',
            'interrupted': '上次绘图被重启打断，结果无法确认。需要重画的话请明确告诉我。',
            'failed': '上次没有成功；需要重画的话请明确告诉我。',
            'generating': '上一张还在生成。'}.get(status, '目前没有待发送的图片。'))

    async def send_image(message, request, original):
        uid = message.from_user.id
        prompt = (PORTRAIT if request.self_portrait else '') + request.prompt
        if not await store.reserve_image(uid, settings.image_hourly_limit, settings.image_cooldown_seconds):
            await message.answer('画得有点密啦，稍等一会儿再来。')
            return
        await store.set_image_task(uid, prompt, 'generating')
        try:
            async with image_slots:
                await message.bot.send_chat_action(message.chat.id, 'upload_photo')
                ref = str(reference_path(settings)) if request.self_portrait else None
                data = await brain.image(prompt, ref, quality=settings.image_quality)
                cached = Path(settings.database_path).parent / f'image_{uid}.png'
                temp = cached.with_suffix('.tmp')
                temp.write_bytes(data)
                temp.replace(cached)
            await store.set_image_task(uid, prompt, 'generated')
            await send_cached(message, (prompt, 'generated'))
            await store.add_exchange(uid, original, '[已生成图片；是否发送成功以图片任务状态为准]', settings.max_context_messages)
        except Exception as exc:
            await store.set_image_task(uid, prompt, 'failed')
            report_error('image_generation_failed', exc)
            await message.answer(error_message(exc))

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
            task = await store.image_task(uid)
            previous, remaining = await store.character_state(uid)
            mood, remaining = next_mood(text, previous, remaining)
            await message.bot.send_chat_action(message.chat.id, 'typing')
            answer = await brain.chat(history, memories, text, mood, image_task=task)
            if isinstance(answer, ImageRequest):
                await send_image(message, answer, text)
                return
            if isinstance(answer, ImageStatus):
                await send_cached(message, task)
                return
            for offset in range(0, len(answer), 3800):
                await message.answer(answer[offset:offset+3800])
            await store.set_character_state(uid, mood, remaining)
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
    brain = Brain(settings.openai_api_key, settings.text_model, settings.image_model,
                  (ROOT / 'personas/gptchan.md').read_text(encoding='utf-8'))
    bot = Bot(settings.telegram_bot_token)
    dp = Dispatcher()
    dp.include_router(build_router(settings, store, brain))
    try:
        await bot.get_me()
        await bot.delete_webhook(drop_pending_updates=False)
        log.info('bot_ready allowed_users=%d text_model=%s image_model=%s reference=%s',
                 len(settings.allowed_ids), settings.text_model, settings.image_model, reference_path(settings).is_file())
        if settings.run_startup_probe:
            from app.probe import startup_probe
            await startup_probe(brain, Path(settings.database_path).parent, reference_path(settings))
        await dp.start_polling(bot)
    finally:
        await brain.client.close()
        await bot.session.close()

if __name__ == '__main__':
    asyncio.run(main())
