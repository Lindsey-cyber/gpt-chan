import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import SendPhoto

from app.ai import Brain, ImageRequest
from app.main import build_router
from app.personas import resolve_profile, profile_prompt
from app.storage import Storage
from test_image_intent import settings, Session, feed, png


def test_switch_persists_and_keeps_memories_history_and_separate_moods(tmp_path):
    async def case():
        cfg=settings(tmp_path);store=Storage(cfg.database_path);await store.init()
        await store.save_memories(1,['喜欢蓝色'],12)
        await store.add_exchange(1,'原来的话','原来的答复',24)
        await store.set_character_state(1,'bashful-proud',3)
        brain=NS(chat=AsyncMock(return_value='主人，在呀♡'),extract_memory=AsyncMock(return_value=[]),image=AsyncMock(return_value=png()))
        session=Session();bot=Bot(cfg.telegram_bot_token,session=session)
        dp=Dispatcher();dp.include_router(build_router(cfg,store,brain))
        assert await store.selected_persona(1)=='bailongji'
        await feed(bot,dp,'/persona 小白')
        assert await Storage(cfg.database_path).selected_persona(1)=='xiaobai'
        assert await store.selected_persona(2)=='bailongji'
        assert (await store.memories(1,12))[0][1]=='喜欢蓝色'
        assert len(await store.history(1,24,32000))==2
        assert await store.character_state(1,'xiaobai')==('steady',0)
        await feed(bot,dp,'摸摸头')
        assert brain.chat.call_args.kwargs['persona']=='xiaobai'
        assert await store.character_state(1,'xiaobai')==('soft-shy',2)
        await feed(bot,dp,'/persona 白龙姬')
        assert await store.character_state(1)==('bashful-proud',3)
        await feed(bot,dp,'你好')
        assert brain.chat.call_args.kwargs['persona']=='bailongji'
        await feed(bot,dp,'/persona nonsense')
        await feed(bot,dp,'/persona 小白',uid=99)
        assert await store.selected_persona(1)=='bailongji'
        assert await store.selected_persona(99)=='bailongji'
        for name in ('小白','白龙姬'):
            await feed(bot,dp,'/persona '+name)
            brain.chat.return_value=ImageRequest('自画像',True)
            await feed(bot,dp,'画一张自画像')
        assert brain.image.call_count==2
        assert all(call.args[1].endswith('gptchan_reference.png') for call in brain.image.call_args_list)
        assert sum(isinstance(call,SendPhoto) for call in session.calls)==2
        await feed(bot,dp,'/new')
        assert await store.selected_persona(1)=='bailongji'
        assert (await store.memories(1,12))[0][1]=='喜欢蓝色'
        await bot.session.close()
    asyncio.run(case())


@pytest.mark.parametrize('key', ['bailongji','xiaobai'])
def test_prompt_choice_is_per_request_and_never_mutates_shared_brain(key):
    async def case():
        brain=Brain('fake','gpt-5.2','image','original shared persona')
        with patch.object(brain.client.responses,'create',AsyncMock(return_value=NS(output=[],output_text='主人♡'))) as create:
            await brain.chat([{'role':'assistant','content':'旧名字旧人格'}],[],'你是谁嘛',persona=key)
            prompt=create.call_args.kwargs['instructions']
            assert prompt.startswith(profile_prompt(key))
            assert brain.persona=='original shared persona'
            assert '当前选定人格是'+('小白' if key=='xiaobai' else '白龙姬') in prompt
        await brain.client.close()
    asyncio.run(case())


def test_aliases_are_validated():
    assert resolve_profile('grok')=='xiaobai'
    assert resolve_profile('白龙姬')=='bailongji'
    assert resolve_profile('../../secrets') is None


def test_profile_probe_runs_once_and_checks_both_profiles(tmp_path):
    async def case():
        from app.profile_probe import profile_probe
        import json
        replies=['主人，白龙姬在呀♡','主人！小白在这里嘛♡','哼嗯～主人再摸一下嘛♡',
            '主人，x未定义，先赋值x=1呀♡',ImageRequest('小白自画像',True),
            '主人，白龙姬回来啦♡','21呀，主人！']
        brain=NS(text_model='test',chat=AsyncMock(side_effect=replies))
        await profile_probe(brain,tmp_path)
        await profile_probe(brain,tmp_path)
        assert brain.chat.call_count==7
        assert brain.chat.call_args.kwargs['mood']=='attentive'
        assert [c.kwargs['persona'] for c in brain.chat.call_args_list]==['bailongji','xiaobai','xiaobai','xiaobai','xiaobai','bailongji','bailongji']
        assert json.loads(next(tmp_path.glob('profile-probe-*.json')).read_text())['status']=='passed'
    asyncio.run(case())
