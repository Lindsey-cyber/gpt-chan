import asyncio
import base64
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from aiogram.methods import SendMessage, SendPhoto

from app import novelai
from app.ai import Brain, ImageRequest
from app.main import PORTRAIT
from app.storage import Storage
from app.tool_errors import ToolProtocolError
from test_image_intent import setup, feed, png
from test_image_reliability import archive, transport, Stream


@pytest.mark.parametrize('nsfw', [False, True])
@pytest.mark.parametrize('self_portrait', [False, True])
def test_complete_backend_and_identity_matrix(tmp_path, monkeypatch, nsfw, self_portrait):
    async def case():
        cfg, store, planner, session, bot, dp = await setup(tmp_path)
        cfg.novelai_token = 'pst-test'
        brain = Brain('fake', cfg.text_model, cfg.image_model, 'persona', novelai_token='pst-test')
        planner.image = brain.image
        planner.chat.return_value = ImageRequest('requested scene', self_portrait, nsfw)
        result = NS(data=[NS(b64_json=base64.b64encode(png()).decode())])
        images = NS(generate=AsyncMock(return_value=result), edit=AsyncMock(return_value=result))
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(201, stream=Stream([archive()]))
        transport(monkeypatch, handler)
        try:
            with patch.object(brain.client, 'with_options', return_value=NS(images=images)):
                await feed(bot, dp, '画指定场景')
            assert await store.image_route(1) == {'nsfw': nsfw, 'self_portrait': self_portrait}
            if nsfw:
                assert len(requests) == 1
                images.generate.assert_not_awaited()
                images.edit.assert_not_awaited()
                payload = requests[0]
                assert ('white dragon horns' in payload['input']) is self_portrait
                assert PORTRAIT not in payload['input']
                assert payload['model'] == 'nai-diffusion-4-5-full'
                params = payload['parameters']
                assert params['v4_prompt']['caption']['base_caption'] == payload['input']
                assert params['v4_negative_prompt']['caption']['base_caption'] == params['negative_prompt']
                assert params['image_format'] == 'png' and params['n_samples'] == 1
                assert type(params['seed']) is int and 0 <= params['seed'] < 2**32
            else:
                assert requests == []
                operation = images.edit if self_portrait else images.generate
                other = images.generate if self_portrait else images.edit
                operation.assert_awaited_once()
                other.assert_not_awaited()
                args = operation.call_args.kwargs
                assert args['prompt'] == (PORTRAIT if self_portrait else '') + 'requested scene'
                assert ('image' in args) is self_portrait
                assert args['model'] == cfg.image_model and args['quality'] == cfg.image_quality
            assert sum(isinstance(c, SendPhoto) for c in session.calls) == 1
        finally:
            await brain.client.close()
            await bot.session.close()
    asyncio.run(case())


@pytest.mark.parametrize('arguments', [
    {'prompt': 'scene', 'self_portrait': False},
    {'prompt': 'scene', 'self_portrait': False, 'nsfw': None},
    {'prompt': 'scene', 'self_portrait': False, 'nsfw': 'false'},
    {'prompt': 'scene', 'self_portrait': False, 'nsfw': 0},
])
def test_missing_or_ambiguous_routing_flags_never_default_to_a_backend(arguments):
    async def case():
        brain = Brain('fake', 'text', 'image', 'persona')
        response = NS(output=[NS(type='function_call', name='generate_image', arguments=json.dumps(arguments))])
        try:
            with patch.object(brain.client.responses, 'create', AsyncMock(return_value=response)):
                with pytest.raises(ToolProtocolError):
                    await brain.chat([], [], '画指定场景')
        finally:
            await brain.client.close()
    asyncio.run(case())


@pytest.mark.parametrize('token', ['', '   ', 'invalid-token'])
def test_missing_backend_configuration_does_not_consume_attempt_or_replace_cache(tmp_path, token):
    async def case():
        cfg, store, brain, session, bot, dp = await setup(tmp_path)
        cfg.novelai_token = token
        brain.chat.return_value = ImageRequest('first scene', False)
        try:
            await feed(bot, dp, '画第一张')
            previous = await store.image_task(1)
            task_id = await store.image_task_id(1)
            brain.chat.return_value = ImageRequest('second scene', False, True)
            with patch.object(store, 'reserve_image', AsyncMock()) as reserve:
                await feed(bot, dp, '画另一张', number=2)
                reserve.assert_not_awaited()
            assert await store.image_task(1) == previous
            assert await store.image_task_id(1) == task_id
            assert brain.image.call_count == 1
            assert any('没有切换到其他后端' in c.text for c in session.calls if isinstance(c, SendMessage))
        finally:
            await bot.session.close()
    asyncio.run(case())


@pytest.mark.parametrize('nsfw', [False, True])
def test_provider_rejection_never_falls_back_to_the_other_backend(monkeypatch, nsfw):
    async def case():
        brain = Brain('fake', 'text', 'image', 'persona', novelai_token='pst-test')
        images = NS(generate=AsyncMock(side_effect=ValueError('synthetic rejection')), edit=AsyncMock())
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(401, stream=Stream([]))
        transport(monkeypatch, handler)
        try:
            with patch.object(brain.client, 'with_options', return_value=NS(images=images)):
                with pytest.raises(RuntimeError if nsfw else ValueError):
                    await brain.image('requested scene', nsfw=nsfw)
            assert len(requests) == int(nsfw)
            assert images.generate.call_count == int(not nsfw)
            images.edit.assert_not_awaited()
        finally:
            await brain.client.close()
    asyncio.run(case())


def test_route_context_survives_restart_and_is_passed_to_continuations(tmp_path):
    async def case():
        cfg, store, brain, session, bot, dp = await setup(tmp_path)
        cfg.novelai_token = 'pst-test'
        brain.chat.return_value = ImageRequest('first scene', True, True)
        try:
            await feed(bot, dp, '画第一张')
            reopened = Storage(store.path)
            await reopened.init()
            assert await reopened.image_route(1) == {'nsfw': True, 'self_portrait': True}
            assert await reopened.image_route(2) is None
            brain.chat.return_value = '确认下一张的要求'
            await feed(bot, dp, '继续出图', number=2)
            assert brain.chat.call_args.kwargs['image_route'] == {'nsfw': True, 'self_portrait': True}
            await feed(bot, dp, '/new', number=3)
            assert await reopened.image_route(1) is None
        finally:
            await bot.session.close()
    asyncio.run(case())


def test_v4_prompt_has_explicit_conditioning_and_seed_is_preserved(monkeypatch):
    async def case():
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(201, stream=Stream([archive()]))
        transport(monkeypatch, handler)
        with patch.object(novelai.secrets, 'randbelow', side_effect=[123, 456]):
            await novelai.generate('pst-test', 'scene', seed=0)
            await novelai.generate('pst-test', 'scene')
            await novelai.generate('pst-test', 'scene')
        assert [r['parameters']['seed'] for r in requests] == [0, 123, 456]
        assert all(r['parameters']['v4_prompt']['caption']['base_caption'] == r['input'] for r in requests)
    asyncio.run(case())
