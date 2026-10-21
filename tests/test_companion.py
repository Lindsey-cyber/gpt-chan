import asyncio
from datetime import datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage

from app.ai import Brain
from app.companion import Companion, CompanionStore, due_event, local_day
from app.main import build_router, UserSerial
from app.storage import Storage
from test_image_intent import settings, Session, feed


def stamp(hour, minute=0, day=21):
    return datetime(2026,10,day,hour,minute,tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()


def user(now, **kwargs):
    return {'user_id':1,'chat_id':1,'timezone':'Asia/Tokyo','enabled':1,
        'last_inbound':now-7*3600,'last_affection':None,'last_attempt':0,
        'snooze_until':0,'day_greeted':None,**kwargs}


@pytest.mark.parametrize('hour', [2,3,7])
def test_quiet_hours(hour):
    now=stamp(hour)
    assert due_event(user(now),now,set()) is None


def test_night_requires_recent_received_message_not_presence():
    now=stamp(23,30)
    assert due_event(user(now),now,set()) is None
    recent=user(now,last_inbound=now-120)
    key,kind=due_event(recent,now,set())
    assert kind=='night'
    # The same night spans midnight, so restarting cannot send it twice.
    after=stamp(0,30,day=22)
    assert due_event(user(after,last_inbound=after-120),after,{key}) is None


def test_active_conversation_and_greeted_morning_are_quiet():
    now=stamp(9,0)
    assert due_event(user(now,last_inbound=now-10),now,set()) is None
    assert due_event(user(now,day_greeted='2026-10-21'),now,set()) is None
    assert due_event(user(now,last_attempt=now-3600),now,set()) is None
    assert due_event(user(now,enabled=0),now,set()) is None
    assert due_event(user(now,snooze_until=now+3600),now,set()) is None


def test_affection_and_timezone():
    now=stamp(16)
    event=due_event(user(now,last_inbound=now-1800,last_affection=now-1800),now,set())
    assert event==('2026-10-21:affection','affection')
    assert local_day(stamp(0,30),'UTC')=='2026-10-20'


async def prepare(tmp_path):
    cfg=settings(tmp_path)
    store=Storage(cfg.database_path)
    await store.init()
    state=CompanionStore(cfg.database_path)
    await state.init()
    return cfg,store,state


def test_atomic_claim_cap_dedup_restart_and_user_isolation(tmp_path):
    async def case():
        cfg,store,state=await prepare(tmp_path)
        now=stamp(9)
        for uid in (1,2): await state.activity(uid,uid,now-7200,'Asia/Tokyo',True)
        claims=await asyncio.gather(*(state.claim(1,1,'same','2026-10-21',now,4) for _ in range(5)))
        assert sum(claims)==1
        restart=CompanionStore(cfg.database_path)
        assert not await restart.claim(1,1,'same','2026-10-21',now+7200,4)
        for n in range(1,4):
            assert await restart.claim(1,1,f'event{n}','2026-10-21',now+n*7200,4)
        assert not await restart.claim(1,1,'fifth','2026-10-21',now+8*3600,4)
        assert await restart.claim(2,2,'same','2026-10-21',now,4)
        assert not await restart.claim(2,999,'wrong-chat','2026-10-22',now+86400,4)
        await state.configure(1,enabled=False)
        await state.activity(1,1,now+86400,'Asia/Tokyo',True)
        assert not next(u for u in await state.users() if u['user_id']==1)['enabled']
        assert await state.greet(1,'2026-10-21')
        assert not await restart.greet(1,'2026-10-21')
        assert await state.greet(2,'2026-10-21')
    asyncio.run(case())


def test_only_verified_private_users_register_and_can_control_own_settings(tmp_path):
    async def case():
        cfg,store,state=await prepare(tmp_path)
        brain=NS(chat=AsyncMock(return_value='主人！小白在呀♡'),extract_memory=AsyncMock(return_value=[]))
        session=Session(); bot=Bot(cfg.telegram_bot_token,session=session)
        dp=Dispatcher(); serial=UserSerial(cfg,state)
        dp.include_router(build_router(cfg,store,brain,serial,state))
        await feed(bot,dp,'你好',uid=99)
        await feed(bot,dp,'你好',uid=1,chat={'id':-111,'type':'group'})
        assert await state.users()==[]
        await feed(bot,dp,'/persona 小白',uid=1)
        await feed(bot,dp,'摸摸头',uid=1)
        assert brain.chat.call_args.kwargs['daily_greeting'] is True
        await feed(bot,dp,'继续',uid=1)
        assert brain.chat.call_args.kwargs['daily_greeting'] is False
        await feed(bot,dp,'/proactive off',uid=1)
        await feed(bot,dp,'你好',uid=2)
        await feed(bot,dp,'/timezone Asia/Shanghai',uid=2)
        rows={u['user_id']:u for u in await state.users()}
        assert rows[1]['enabled']==0 and rows[1]['last_affection']
        assert rows[2]['enabled']==0 and rows[2]['timezone']=='Asia/Shanghai'
        await feed(bot,dp,'/timezone bad/timezone',uid=2)
        assert next(u for u in await state.users() if u['user_id']==2)['timezone']=='Asia/Shanghai'
        await feed(bot,dp,'再来聊天',uid=1)
        assert next(u for u in await state.users() if u['user_id']==1)['enabled']==0
        await feed(bot,dp,'/proactive on',uid=1)
        assert next(u for u in await state.users() if u['user_id']==1)['enabled']==1
        await bot.session.close()
    asyncio.run(case())


@pytest.mark.parametrize('failure', ['none','timeout','blocked','rate_limit','generation'])
def test_scheduler_delivery_failure_and_no_replay(tmp_path,failure):
    async def case():
        cfg,store,state=await prepare(tmp_path)
        now=stamp(16)
        await state.activity(1,111,now-1800,'Asia/Tokyo',True,affection=True)
        await store.save_memories(1,['喜欢蓝色'],12)
        await store.save_memories(2,['喜欢红色'],12)
        brain=NS(proactive=AsyncMock(return_value='主人，刚才摸过的小白还开心着呢♡'))
        bot=NS(send_message=AsyncMock())
        if failure=='timeout': bot.send_message.side_effect=TimeoutError()
        if failure=='blocked': bot.send_message.side_effect=TelegramForbiddenError(method=SendMessage(chat_id=111,text='x'),message='blocked')
        if failure=='rate_limit': bot.send_message.side_effect=TelegramRetryAfter(method=SendMessage(chat_id=111,text='x'),message='limited',retry_after=100)
        if failure=='generation': brain.proactive.side_effect=TimeoutError()
        companion=Companion(cfg,state,store,brain,bot,{})
        await companion.tick(now)
        await Companion(cfg,CompanionStore(cfg.database_path),store,brain,bot,{}).tick(now+60)
        assert bot.send_message.call_count==1
        assert bot.send_message.call_args.args[0]==111
        assert brain.proactive.call_args.args[2][0][1]=='喜欢蓝色'
        assert await store.history(2,24,32000)==[]
        history=await store.history(1,24,32000)
        assert bool(history)==(failure in ('none','generation'))
        if history: assert history[-1]['role']=='assistant'
        row=(await state.users())[0]
        if failure=='blocked': assert not row['enabled']
        if failure=='rate_limit': assert row['snooze_until']==now+100
    asyncio.run(case())


def test_proactive_model_call_is_bounded_and_cannot_generate_images():
    async def case():
        brain=Brain('fake','gpt-5.2','image','小白')
        client=NS(responses=NS(create=AsyncMock(return_value=NS(output_text='主人早安♡'))))
        with patch.object(brain.client,'with_options',return_value=client) as options:
            assert await brain.proactive('morning',[],[])=='主人早安♡'
            args=client.responses.create.call_args.kwargs
            assert args['store'] is False and 'tools' not in args
            assert args['max_output_tokens']==220
            assert options.call_args.kwargs['max_retries']==0
        await brain.client.close()
    asyncio.run(case())
