import asyncio
import base64
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage, SendPhoto
from aiogram.types import Message, Update

from app.ai import Brain, ImageRequest, ImageStatus
from app.config import Settings
from app.main import build_router, reference_path, save_reference
from app.storage import Storage


def run(coro):
    return asyncio.run(coro)


def png():
    out = BytesIO()
    Image.new('RGB', (16, 16)).save(out, 'PNG')
    return out.getvalue()


class Session(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.fail_photo = False

    async def close(self): pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, SendPhoto) and self.fail_photo:
            raise OSError('delivery interrupted')
        if isinstance(method, (SendMessage, SendPhoto)):
            return Message(message_id=100, date=0, chat={'id': method.chat_id, 'type': 'private'})
        return True

    async def stream_content(self, *args, **kwargs):
        yield b''


def settings(tmp_path):
    return Settings(_env_file=None, telegram_bot_token='123456:fake-token', openai_api_key='fake', allowed_user_ids='1,2', database_path=str(tmp_path/'bot.sqlite3'), image_cooldown_seconds=0)


async def setup(tmp_path):
    cfg = settings(tmp_path)
    store = Storage(cfg.database_path)
    await store.init()
    brain = NS(chat=AsyncMock(return_value='在呀。'), image=AsyncMock(return_value=png()), extract_memory=AsyncMock(return_value=[]))
    session = Session()
    bot = Bot(cfg.telegram_bot_token, session=session)
    dp = Dispatcher()
    dp.include_router(build_router(cfg, store, brain))
    return cfg, store, brain, session, bot, dp


async def feed(bot, dp, text, uid=1, number=1, **kwargs):
    message = {'message_id': number, 'date': 0, 'chat': {'id': uid, 'type': 'private'}, 'from': {'id': uid, 'is_bot': False, 'first_name': 'test'}, **kwargs}
    if text is not None:
        message['text'] = text
    return await dp.feed_update(bot, Update(update_id=number, message=message))


def test_tool_schema_and_context():
    async def case():
        brain = Brain('fake', 'gpt-4.1-mini', 'gpt-image-1-mini', '角色')
        response = NS(output=[NS(type='function_call', name='generate_image', arguments='{"prompt":"白龙姬精致肖像", "self_portrait":true}')], output_text='这是提示词，不应发送')
        with patch.object(brain.client.responses, 'create', AsyncMock(return_value=response)) as create:
            answer = await brain.chat([{'role':'user','content':'自画像'}], [], '精致一点', image_task=('原图','sent'))
            assert answer == ImageRequest('白龙姬精致肖像', True)
            args = create.call_args.kwargs
            assert args['parallel_tool_calls'] is False
            assert args['store'] is False
            assert args['input'][-1]['content'] == '精致一点'
            assert all(t['strict'] for t in args['tools'])
        await brain.client.close()
    run(case())


@pytest.mark.parametrize('arguments', ['{}', '{"prompt":"", "self_portrait":true}', '{"prompt":"x", "self_portrait":"yes"}', '{"prompt":"x", "self_portrait":true, "path":"/etc/passwd"}'])
def test_bad_tool_arguments_rejected(arguments):
    async def case():
        brain = Brain('fake','text','image','')
        response = NS(output=[NS(type='function_call', name='generate_image', arguments=arguments)])
        with patch.object(brain.client.responses, 'create', AsyncMock(return_value=response)):
            with pytest.raises(ValueError):
                await brain.chat([], [], '画画')
        await brain.client.close()
    run(case())


def test_image_generation_and_reference_edit():
    async def case():
        brain = Brain('fake','text','gpt-image-1-mini','')
        result = NS(data=[NS(b64_json=base64.b64encode(png()).decode())], usage=None)
        client = NS(images=NS(generate=AsyncMock(return_value=result), edit=AsyncMock(return_value=result)))
        with patch.object(brain.client, 'with_options', return_value=client) as options:
            assert await brain.image('风景') == png()
            assert await brain.image('自画像', str(Path('assets/gptchan_reference.png'))) == png()
            assert client.images.edit.call_count == 1
            assert client.images.generate.call_count == 1
            assert options.call_args.kwargs['max_retries'] == 0
            assert client.images.edit.call_args.kwargs['model'] == 'gpt-image-1-mini'
        client.images.generate.return_value = NS(data=[])
        with patch.object(brain.client,'with_options',return_value=client):
            with pytest.raises(ValueError): await brain.image('x')
        await brain.client.close()
    run(case())


def test_dispatch_image_refinement_and_user_isolation(tmp_path):
    async def case():
        cfg, store, brain, session, bot, dp = await setup(tmp_path)
        brain.chat.return_value = ImageRequest('白龙姬肖像', True)
        await feed(bot, dp, '画一张你的自画像')
        brain.chat.return_value = ImageRequest('白龙姬肖像，精致光影', True)
        await feed(bot, dp, '精致一点', number=2)
        await feed(bot, dp, '继续出图', number=3)
        assert brain.image.call_count == 3
        assert all(c.args[1].endswith('gptchan_reference.png') for c in brain.image.call_args_list)
        assert brain.chat.call_args.kwargs['image_task'][1] == 'sent'
        assert (await store.image_task(1))[1] == 'sent'
        assert await store.image_task(2) is None
        assert await store.history(2,24,32000) == []
        assert sum(isinstance(c,SendPhoto) for c in session.calls) == 3
        assert not any('白龙姬肖像' in (getattr(c,'text','') or '') for c in session.calls)
        await bot.session.close()
    run(case())


def test_failed_delivery_reuses_cached_bytes(tmp_path):
    async def case():
        cfg, store, brain, session, bot, dp = await setup(tmp_path)
        session.fail_photo = True
        brain.chat.return_value = ImageRequest('云海',False)
        await feed(bot,dp,'画云海')
        assert (await store.image_task(1))[1] == 'delivery_failed'
        session.fail_photo = False
        brain.chat.return_value = ImageStatus()
        await feed(bot,dp,'图片呢',number=2)
        assert brain.image.call_count == 1
        assert (await store.image_task(1))[1] == 'sent'
        await bot.session.close()
    run(case())


def test_generation_failure_and_status_does_not_retry(tmp_path):
    async def case():
        cfg,store,brain,session,bot,dp = await setup(tmp_path)
        brain.chat.return_value=ImageRequest('云海',False)
        brain.image.side_effect=TimeoutError()
        await feed(bot,dp,'画云海')
        assert (await store.image_task(1))[1] == 'unknown'
        brain.chat.return_value=ImageStatus()
        await feed(bot,dp,'图片呢',number=2)
        assert brain.image.call_count == 1
        await bot.session.close()
    run(case())


def test_auth_commands_and_reference_routing(tmp_path):
    async def case():
        cfg,store,brain,session,bot,dp=await setup(tmp_path)
        await feed(bot,dp,'画自己',uid=99)
        await feed(bot,dp,'/setref')
        with patch('app.main.save_reference',AsyncMock()) as save:
            await feed(bot,dp,None,number=3,caption='/setref',photo=[{'file_id':'abc','file_unique_id':'u','width':16,'height':16}])
            save.assert_awaited_once()
        brain.chat.assert_not_awaited()
        assert len(session.calls)==2
        await bot.session.close()
    run(case())


def test_atomic_reference_preserves_previous_on_failure(tmp_path):
    async def case():
        target=tmp_path/'reference.jpg'
        target.write_bytes(png())
        async def bad_download(photo,destination):
            destination.write_bytes(b'broken')
            raise OSError()
        with pytest.raises(OSError):
            await save_reference(NS(download=bad_download),'photo',target)
        assert target.read_bytes()==png()
        assert not list(tmp_path.glob('.reference-*'))
        async def good_download(photo,destination): destination.write_bytes(png())
        await save_reference(NS(download=good_download),'photo',target)
        assert reference_path(settings(tmp_path)).is_file()
    run(case())


def test_storage_upgrade_backup_memory_and_counters(tmp_path):
    async def case():
        store=Storage(str(tmp_path/'old.sqlite3'))
        await store.init()
        await store.save_memories(1,['喜欢蓝色'],12)
        await store.save_memories(2,['喜欢红色'],12)
        await store.set_character_state(1,'soft-shy',2)
        await store.set_image_task(1,'肖像','generating')
        await store.init()
        assert Path(store.path+'.pre-reliability.bak').is_file()
        assert (await store.image_task(1))[1]=='interrupted'
        assert (await store.memories(1,12))[0][1]=='喜欢蓝色'
        assert await store.character_state(1)==('soft-shy',2)
        for i in range(16):
            await store.add_exchange(1,str(i),'答',24)
            assert await store.should_extract(1)==((i+1)%4==0)
        await store.clear_history(1)
        assert (await store.memories(1,12))[0][1]=='喜欢蓝色'
        other=(await store.memories(2,12))[0]
        await store.forget(1,other[0])
        assert await store.memories(2,12)==[other]
    run(case())


def test_rate_limit_is_persistent_and_per_user(tmp_path):
    async def case():
        store=Storage(str(tmp_path/'bot.sqlite3'))
        await store.init()
        assert await store.reserve_image(1,2,0)
        assert not await store.reserve_image(1,2,15)
        assert await store.reserve_image(1,2,0)
        assert not await Storage(store.path).reserve_image(1,2,0)
        assert await store.reserve_image(2,2,0)
    run(case())


def test_new_serializes_with_image(tmp_path):
    async def case():
        cfg,store,brain,session,bot,dp=await setup(tmp_path)
        entered,release=asyncio.Event(),asyncio.Event()
        async def image(*args,**kwargs):
            entered.set()
            await release.wait()
            return png()
        brain.chat.return_value=ImageRequest('肖像',True)
        brain.image.side_effect=image
        drawing=asyncio.create_task(feed(bot,dp,'画我',number=1))
        await entered.wait()
        clearing=asyncio.create_task(feed(bot,dp,'/new',number=2))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(drawing,clearing)
        assert await store.history(1,24,32000)==[]
        assert await store.image_task(1) is None
        await bot.session.close()
    run(case())


def test_probe_runs_once_without_telegram_delivery(tmp_path):
    async def case():
        from app.probe import startup_probe, probe_marker
        brain=NS(text_model='text',image_model='gpt-image-1-mini',
            chat=AsyncMock(side_effect=['你好',ImageRequest('肖像',True),ImageRequest('精致肖像',True),ImageRequest('继续肖像',True)]),
            image=AsyncMock(return_value=png()))
        await startup_probe(brain,tmp_path,Path('assets/gptchan_reference.png'))
        await startup_probe(brain,tmp_path,Path('assets/gptchan_reference.png'))
        assert brain.chat.call_count==4
        assert brain.image.call_count==1
        assert 'passed' in probe_marker(tmp_path,brain.image_model,'high').read_text()
    run(case())


def test_startup_initializes_and_enters_polling(tmp_path):
    async def case():
        from app.main import main
        cfg=settings(tmp_path)
        session=Session()
        bot=Bot(cfg.telegram_bot_token,session=session)
        brain=NS(client=NS(close=AsyncMock()))
        with patch('app.main.Settings',return_value=cfg), patch('app.main.Bot',return_value=bot), patch('app.main.Brain',return_value=brain), patch.object(Dispatcher,'start_polling',AsyncMock()) as poll:
            await main()
            poll.assert_awaited_once_with(bot)
            brain.client.close.assert_awaited_once()
        assert Path(cfg.database_path).is_file()
    run(case())


def test_upgraded_default_image_contract(tmp_path):
    async def case():
        cfg=settings(tmp_path)
        assert cfg.image_model == 'gpt-image-2.5-sunburst'
        assert cfg.image_quality == 'high'
        brain=Brain('fake',cfg.text_model,cfg.image_model,'')
        result=NS(data=[NS(b64_json=base64.b64encode(png()).decode())],usage=None)
        client=NS(images=NS(edit=AsyncMock(return_value=result)))
        with patch.object(brain.client,'with_options',return_value=client):
            await brain.image('肖像',str(reference_path(cfg)),quality=cfg.image_quality)
        args=client.images.edit.call_args.kwargs
        assert args['model']=='gpt-image-2.5-sunburst'
        assert args['quality']=='high'
        assert args['output_format']=='png'
        assert args['n']==1
        assert 'input_fidelity' not in args
        await brain.client.close()
    run(case())


def test_probe_marker_tracks_model_and_quality(tmp_path):
    from app.probe import probe_marker
    assert probe_marker(tmp_path,'old','medium') != probe_marker(tmp_path,'new','high')
    assert probe_marker(tmp_path,'new','medium') != probe_marker(tmp_path,'new','high')
    assert probe_marker(tmp_path,'new','high') == probe_marker(tmp_path,'new','high')


@pytest.mark.parametrize('model,supported', [('gpt-5.2',True),('gpt-5.2-2025-12-11',True),('gpt-4.1-mini',False)])
def test_low_verbosity_only_sent_to_supported_text_model(model,supported):
    async def case():
        brain=Brain('fake',model,'image','persona')
        response=NS(output=[],output_text='在呀。')
        with patch.object(brain.client.responses,'create',AsyncMock(return_value=response)) as create:
            assert await brain.chat([],[],'在吗')=='在呀。'
            args=create.call_args.kwargs
            assert ('text' in args)==supported
            if supported: assert args['text']=={'verbosity':'low'}
            assert args['max_output_tokens']==1600
        await brain.client.close()
    run(case())
