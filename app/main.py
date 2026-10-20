import asyncio
import logging
import re
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, Message

from app.ai import Brain
from app.config import Settings
from app.storage import Storage

log = logging.getLogger(__name__)

def requested_image(text: str) -> bool:
    return bool(re.search(r'(?:帮我|给我|请|现在)?(?:画|绘制|生成|做)(?:一张|一幅|个)?(?:图片|图|插画|头像|壁纸)|(?:画一张|画一幅|画个)|(?:生图)', text))

def build_router(settings: Settings, store: Storage, brain: Brain) -> Router:
    router = Router()
    locks: dict[int, asyncio.Lock] = {}

    def permitted(message: Message) -> bool:
        return bool(message.from_user and message.from_user.id in settings.allowed_ids and message.chat.type == 'private')

    @router.message(Command('id'))
    async def identity(message: Message):
        if message.from_user:
            await message.answer(f'你的 Telegram ID: {message.from_user.id}')

    @router.message(CommandStart())
    async def start(message: Message):
        if permitted(message):
            await message.answer('哼，主人终于来了？本龙娘才没有等你呢。想聊什么？')

    @router.message(Command('new'))
    async def new(message: Message):
        if not permitted(message): return
        await store.clear_history(message.from_user.id)
        await message.answer('新的对话开始了，主人。旧的短期上下文清空啦，长期记忆还在。')

    @router.message(Command('memories'))
    async def memories(message: Message):
        if not permitted(message): return
        rows = await store.memories(message.from_user.id, settings.memory_limit)
        await message.answer('本龙娘记得这些：\n'+('\n'.join(f'{i}. {f}' for i,f in rows) if rows else '（还没有长期记忆）'))

    @router.message(Command('forget'))
    async def forget(message: Message):
        if not permitted(message): return
        arg = (message.text or '').split(maxsplit=1)
        if len(arg)!=2 or (arg[1]!='all' and not arg[1].isdigit()):
            await message.answer('用 /forget 记忆编号，或 /forget all 清空。');return
        await store.forget(message.from_user.id,None if arg[1]=='all' else int(arg[1]))
        await message.answer('已经按你的要求删除了。')

    async def send_image(message: Message, prompt: str):
        uid = message.from_user.id
        async with locks.setdefault(uid, asyncio.Lock()):
            try:
                await message.bot.send_chat_action(message.chat.id,'upload_photo')
                data = await brain.image(prompt)
                await message.answer_photo(BufferedInputFile(data,'gptchan.png'),caption='画好了，主人。怎么样，本龙娘的审美不错吧？')
            except Exception:
                log.exception('image generation failed')
                await message.answer('图片没生成成功，主人稍后再试吧。')

    @router.message(Command('draw'))
    async def draw(message: Message):
        if not permitted(message): return
        prompt = (message.text or '').partition(' ')[2].strip()
        if not prompt:
            await message.answer('主人，告诉我画什么呀！例如 /draw 蓝色星空下的白龙娘'); return
        if len(prompt)>2000:
            await message.answer('画图描述太长了，缩短一点吧。');return
        await send_image(message, prompt)

    @router.message(F.text)
    async def chat(message: Message):
        if not permitted(message): return
        text=(message.text or '').strip()
        if text.startswith('/'): return
        if len(text)>8000:
            await message.answer('这段太长了，主人分几次发嘛。');return
        if requested_image(text):
            await send_image(message, text)
            return
        uid=message.from_user.id
        async with locks.setdefault(uid,asyncio.Lock()):
            history=await store.history(uid,settings.max_context_messages,settings.max_context_chars)
            memories=await store.memories(uid,settings.memory_limit)
            try:
                await message.bot.send_chat_action(message.chat.id,'typing')
                answer=await brain.chat(history,memories,text)
                await store.add_exchange(uid,text,answer,settings.max_context_messages)
                for offset in range(0,len(answer),3800):
                    await message.answer(answer[offset:offset+3800])
                should_extract = len(history) % 8 >= 6 or any(w in text for w in ('记住', '我喜欢', '我讨厌', '以后请'))
                if should_extract:
                    try:
                        facts=await brain.extract_memory(text,answer,memories)
                        if facts: await store.save_memories(uid,facts,settings.memory_limit)
                    except Exception:
                        log.exception('memory extraction failed')
            except Exception:
                log.exception('chat failed')
                await message.answer('主人，接口暂时出问题了……等一下再试嘛。')
    return router

async def main():
    logging.basicConfig(level=logging.INFO)
    settings=Settings()
    if not settings.allowed_ids: raise RuntimeError('ALLOWED_USER_IDS must not be empty')
    store=Storage(settings.database_path)
    await store.init()
    persona=Path('personas/gptchan.md').read_text(encoding='utf-8')
    brain=Brain(settings.openai_api_key,settings.text_model,settings.image_model,persona)
    bot=Bot(settings.telegram_bot_token)
    dp=Dispatcher()
    dp.include_router(build_router(settings,store,brain))
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__=='__main__':
    asyncio.run(main())
