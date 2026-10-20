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
    t = re.sub(r'\s+', '', text)
    if re.search(r'(怎么|如何|为什么|原理|教程|会不会|能不能|可以吗|解释|介绍|教我).{0,12}(画图|绘画|生图|生成图片)', t):
        return False
    if re.search(r'(画图呢|图片呢|图呢|画好了吗|图片在哪里|图在哪里|画好了没)', t):
        return False
    return bool(re.search(r'(画|绘制|生成|制作|做).{0,20}(自画像|画像|肖像|头像|插画|壁纸|图片|图像|图画|一张图|一幅画)|(画一张|画一幅|画一个|生图|帮我画|给我画|请画|画你自己|画自己|画个你)', t))

def image_followup(text: str) -> bool:
    return bool(re.search(r'(画图|图片|图|画)呢[？?！!]*$|(图片|图)(在哪里|在哪|怎么还没)', text.strip()))

def image_refinement(text: str) -> bool:
    t = text.strip()
    return bool(re.search(r'(精致|细致|细节|更好看|好看一点|可爱一点|写实|二次元|半身|全身|Q版|高清|换个风格|再画|重画|重新画|再来一张|颜色|表情|背景|发型|服装|构图|改一下|调整一下)', t)) and len(t) <= 110

def portrait_prompt(text: str) -> str:
    if re.search(r'(你自己|自己的|你的|白龙娘|白龙姬|GPT娘|你本人|自画像)', text):
        return ('画出同一个固定角色 GPT娘·白龙姬。精致日系二次元人物插画，清冷可爱、自然灵动的少女，银白色柔顺长发，清澈冰蓝眼睛，两枚白色小龙角，白银黑蓝配色的精致改良龙纹服装，优雅细节、干净的线稿、细腻光影、自然面部比例，柔和但有层次的配色。人物长相和服装设计尽量保持一致。不要文字、不要水印、不要无关人物。直接绘制最终成图，不要输出绘图说明。用户的具体构图和风格要求优先：' + text)
    return text

def next_mood(text: str, previous: str, remaining: int) -> tuple[str, int]:
    if any(x in text for x in ('别傲娇','认真一点','别撒娇','严肃一点')):
        return 'focused', 3
    if any(x in text for x in ('难过','好累','焦虑','伤心','崩溃','不开心')):
        return 'caring', 3
    if any(x in text for x in ('摸摸头','摸头','揉揉头','抱抱')):
        return 'soft-shy', 2
    if any(x in text for x in ('你好可爱','你真可爱','夸夸你','你好棒','你真棒','喜欢你')):
        return 'bashful-proud', 3
    if any(x in text for x in ('你输了','你输啦','比一比','挑战你')):
        return 'competitive-playful', 2
    if any(x in text for x in ('我成功了','我做到了','好消息')):
        return 'celebratory', 2
    if any(x in text for x in ('报错','代码','怎么修','解释一下','原理','教程')):
        return 'focused', 1
    if remaining > 1:
        return previous, remaining - 1
    return 'steady', 0

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
            await message.answer('主人来啦。今天想聊什么？')

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
                await store.set_image_task(uid, prompt, 'generating')
                data = await brain.image(portrait_prompt(prompt))
                await message.answer_photo(BufferedInputFile(data,'gptchan.png'),caption='画好啦。要改哪里，直接告诉我就行。')
                await store.set_image_task(uid, prompt, 'sent')
            except Exception:
                await store.set_image_task(uid, prompt, 'failed')
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
        if requested_image(text) or bool(re.search(r'(给我看看你长什么样|画给我看|画出来给我看|想看你的样子|画张你的照片)', text)):

            await send_image(message, text)
            return
        task = await store.image_task(message.from_user.id)
        if task and any(phrase in text for phrase in ('继续出图', '开始出图', '出图吧', '直接出图', '继续画', '开始画', '就按这个画', '确认生成')):
            await send_image(message, task[0])
            return
        if task and image_followup(text):
            if task[1] == 'sent':
                await message.answer('上一张已经作为 Telegram 图片发出啦。要重新生成就说「再画一张」。')
            else:
                await send_image(message, task[0])
            return
        if task and image_refinement(text):
            await send_image(message, task[0] + '。进一步要求：' + text)
            return
        if image_followup(text):
            await message.answer('我这边没有待发送的图片任务。请直接说「画一个……」，我会调用图片功能。')
            return
        uid=message.from_user.id
        async with locks.setdefault(uid,asyncio.Lock()):
            history=await store.history(uid,settings.max_context_messages,settings.max_context_chars)
            memories=await store.memories(uid,settings.memory_limit)
            try:
                await message.bot.send_chat_action(message.chat.id,'typing')
                previous,remaining=await store.character_state(uid)
                mood,remaining=next_mood(text,previous,remaining)
                answer=await brain.chat(history,memories,text,mood)
                await store.set_character_state(uid,mood,remaining)
                await store.add_exchange(uid,text,answer,settings.max_context_messages)
                for offset in range(0,len(answer),3800):
                    await message.answer(answer[offset:offset+3800])
                should_extract = (await store.exchange_count(uid)) % 4 == 0 or any(w in text for w in ('记住', '我喜欢', '我讨厌', '以后请'))
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
