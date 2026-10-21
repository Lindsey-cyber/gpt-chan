import asyncio
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace as NS

import pytest
from aiogram.methods import SendPhoto, SendMessage

from app.ai import Brain, ImageRequest, ImageStatus
from app.api_errors import error_message, error_details
from app.tool_errors import ToolProtocolError
from app.storage import Storage
from test_image_intent import setup, feed


def run(coro):
    return asyncio.run(coro)


def test_sent_picture_does_not_trap_next_normal_chat_in_status(tmp_path):
    async def case():
        cfg,store,brain,session,bot,dp=await setup(tmp_path)
        await store.set_image_task(1,'普通山水画','sent')
        brain.chat.side_effect=[ImageStatus(),'主人，今天也想听你讲讲有趣的事呀♡']
        await feed(bot,dp,'今天过得怎么样？')
        assert brain.chat.call_count==2
        assert brain.chat.call_args.kwargs['status_checked'] is True
        brain.image.assert_not_awaited()
        answers=[c.text for c in session.calls if isinstance(c,SendMessage)]
        assert answers==['主人，今天也想听你讲讲有趣的事呀♡']
        assert (await store.history(1,24,32000))[-1]['content']==answers[-1]
        await bot.session.close()
    run(case())


def test_revision_misclassified_as_status_can_be_resolved_once(tmp_path):
    async def case():
        cfg,store,brain,session,bot,dp=await setup(tmp_path)
        await store.set_image_task(1,'普通山水画，白天','sent')
        brain.chat.side_effect=[ImageStatus(),ImageRequest('同一幅山水画，改为傍晚光线',False)]
        await feed(bot,dp,'把刚才的画改成傍晚光线')
        assert brain.chat.call_count==2 and brain.image.call_count==1
        assert '傍晚' in brain.image.call_args.args[0]
        assert sum(isinstance(c,SendPhoto) for c in session.calls)==1
        await bot.session.close()
    run(case())


def test_status_recovery_is_bounded_and_never_auto_generates_on_error(tmp_path):
    async def case():
        cfg,store,brain,session,bot,dp=await setup(tmp_path)
        brain.chat.return_value=ImageStatus()
        await feed(bot,dp,'你好呀')
        assert brain.chat.call_count==2
        brain.image.assert_not_awaited()
        assert not any('上一张已经发送' in (getattr(c,'text','') or '') for c in session.calls)
        await bot.session.close()
    run(case())


def test_failed_delivery_is_not_recorded_as_sent_and_resend_corrects_context(tmp_path):
    async def case():
        cfg,store,brain,session,bot,dp=await setup(tmp_path)
        brain.chat.return_value=ImageRequest('普通山水画',False)
        session.fail_photo=True
        await feed(bot,dp,'画一幅山水画')
        note=(await store.history(1,24,32000))[-1]['content']
        assert '尚未送达' in note and '已生成并发送' not in note
        session.fail_photo=False
        brain.chat.return_value=ImageStatus()
        await feed(bot,dp,'图片呢')
        assert brain.image.call_count==1
        assert (await store.image_task(1))[1]=='sent'
        assert '缓存重新发送' in (await store.history(1,24,32000))[-1]['content']
        await bot.session.close()
    run(case())


def test_history_failure_after_delivery_does_not_relabel_as_generation_failure(tmp_path):
    async def case():
        cfg,store,brain,session,bot,dp=await setup(tmp_path)
        brain.chat.return_value=ImageRequest('普通山水画',False)
        store.add_exchange=AsyncMock(side_effect=OSError('synthetic storage error'))
        await feed(bot,dp,'画一幅山水画')
        assert (await store.image_task(1))[1]=='sent'
        assert sum(isinstance(c,SendPhoto) for c in session.calls)==1
        assert any('图片已经发给你' in (getattr(c,'text','') or '') for c in session.calls)
        await bot.session.close()
    run(case())


def test_caption_variation_is_per_user_and_survives_reopening_database(tmp_path):
    async def case():
        cfg,store,brain,session,bot,dp=await setup(tmp_path)
        await store.select_persona(1,'xiaobai')
        brain.chat.return_value=ImageRequest('普通山水画',False)
        for i in range(4): await feed(bot,dp,'画一幅山水画',number=i+1)
        captions=[c.caption for c in session.calls if isinstance(c,SendPhoto)]
        assert len(set(captions))==4 and all('小白' in c or '小龙' in c for c in captions)
        reopened=Storage(store.path)
        assert await reopened.next_caption_index(1)==4
        assert await reopened.next_caption_index(2)==0
        await bot.session.close()
    run(case())


@pytest.mark.parametrize('arguments',['{}','[]','null','{"prompt":"x"}','not json'])
def test_bad_tool_payload_has_safe_diagnostic(arguments):
    async def case():
        brain=Brain('fake','text','image','persona')
        response=NS(output=[NS(type='function_call',name='generate_image',arguments=arguments)])
        with patch.object(brain.client.responses,'create',AsyncMock(return_value=response)):
            with pytest.raises(ToolProtocolError) as caught: await brain.chat([],[],'画一幅普通山水画')
        assert error_details(caught.value)['local_reason'] in ('invalid_json','invalid_arguments')
        assert '再说一次' in error_message(caught.value)
        await brain.client.close()
    run(case())


def test_checked_status_cannot_be_queried_again():
    async def case():
        brain=Brain('fake','text','image','persona')
        response=NS(output=[],output_text='主人，傍晚的光线确实更温柔呀♡')
        with patch.object(brain.client.responses,'create',AsyncMock(return_value=response)) as create:
            await brain.chat([],[],'傍晚好美',status_checked=True)
            assert all(t['name']!='image_status' for t in create.call_args.kwargs['tools'])
        await brain.client.close()
    run(case())
