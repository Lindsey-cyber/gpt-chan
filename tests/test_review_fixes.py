import asyncio
import json
import sqlite3
from datetime import datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest
from aiogram.methods import SendMessage, SendPhoto
from openai import AsyncOpenAI, RateLimitError
from pydantic import ValidationError

from app.ai import Brain, ImageRequest, ImageStatus
from app.api_errors import error_message
from app.companion import Companion, CompanionStore
from app.config import Settings
from app.storage import Storage
from app.telegram_text import text_pages
from test_image_intent import setup, feed, png, settings
from test_image_reliability import archive, transport, Stream


@pytest.mark.parametrize('restart', [False, True])
def test_cached_result_recovers_after_generated_state_write_failure(tmp_path, restart):
    async def case():
        cfg, store, brain, session, bot, dp = await setup(tmp_path)
        original = store.set_image_task
        async def fail_generated(uid, prompt, status):
            if status == 'generated':
                raise OSError('synthetic state failure')
            await original(uid, prompt, status)
        brain.chat.return_value = ImageRequest('test', False)
        try:
            with patch.object(store, 'set_image_task', side_effect=fail_generated):
                await feed(bot, dp, '画一张图')
            assert (await store.image_task(1))[1] == 'generating'
            task_id = await store.image_task_id(1)
            assert (tmp_path / f'image_1_{task_id}.png').read_bytes() == png()
            if restart:
                await Storage(store.path).init()
                assert (await store.image_task(1))[1] == 'interrupted'
            brain.chat.return_value = ImageStatus()
            await feed(bot, dp, '图片呢', number=2)
            assert brain.chat.call_args.kwargs['image_task'][1] == 'generated'
            assert (await store.image_task(1))[1] == 'sent'
            assert brain.image.call_count == 1
            assert sum(isinstance(c, SendPhoto) for c in session.calls) == 1
        finally:
            await bot.session.close()
    asyncio.run(case())


def test_failed_new_task_never_resends_previous_task_cache(tmp_path):
    async def case():
        cfg, store, brain, session, bot, dp = await setup(tmp_path)
        try:
            brain.chat.return_value = ImageRequest('first', False)
            await feed(bot, dp, '画第一张')
            first_id = await store.image_task_id(1)
            brain.image.side_effect = TimeoutError()
            await feed(bot, dp, '画第二张', number=2)
            assert await store.image_task_id(1) != first_id
            brain.chat.return_value = ImageStatus()
            await feed(bot, dp, '图片呢', number=3)
            assert brain.image.call_count == 2
            assert sum(isinstance(c, SendPhoto) for c in session.calls) == 1
            assert (await store.image_task(1))[1] == 'unknown'
        finally:
            await bot.session.close()
    asyncio.run(case())


def test_old_schema_and_legacy_cache_remain_usable(tmp_path):
    async def case():
        cfg = settings(tmp_path)
        with sqlite3.connect(cfg.database_path) as db:
            db.execute('CREATE TABLE image_tasks (user_id INTEGER PRIMARY KEY, prompt TEXT NOT NULL, status TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)')
            db.execute("INSERT INTO image_tasks(user_id,prompt,status) VALUES(1,'legacy','delivery_failed')")
        cfg, store, brain, session, bot, dp = await setup(tmp_path)
        (tmp_path / 'image_1.png').write_bytes(png())
        try:
            assert await store.image_task_id(1) is None
            brain.chat.return_value = ImageStatus()
            await feed(bot, dp, '图片呢')
            assert (await store.image_task(1))[1] == 'sent'
            brain.image.assert_not_awaited()
            assert sum(isinstance(c, SendPhoto) for c in session.calls) == 1
        finally:
            await bot.session.close()
    asyncio.run(case())


def test_unknown_image_state_rejected(tmp_path):
    async def case():
        cfg, store, brain, session, bot, dp = await setup(tmp_path)
        try:
            with pytest.raises(ValueError):
                await store.set_image_task(1, 'test', 'typo')
        finally:
            await bot.session.close()
    asyncio.run(case())


@pytest.mark.parametrize('self_portrait', [False, True])
def test_identity_parameter_reaches_backend_without_forced_content_rating(tmp_path, monkeypatch, self_portrait):
    async def case():
        cfg, store, brain, session, bot, dp = await setup(tmp_path)
        cfg.novelai_token = 'pst-test'
        real = Brain('fake', 'text', 'image', 'persona', novelai_token='pst-test')
        brain.image = real.image
        brain.chat.return_value = ImageRequest('a different adult character', self_portrait, True)
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, stream=Stream([archive()]))
        transport(monkeypatch, handler)
        try:
            await feed(bot, dp, '画指定角色')
            prompt = requests[0]['input']
            assert ('white dragon horns' in prompt) is self_portrait
            assert 'a different adult character' in prompt
            assert 'rating:explicit' not in prompt
            assert (await store.image_task(1))[1] == 'sent'
        finally:
            await real.client.close()
            await bot.session.close()
    asyncio.run(case())


def stamp(hour, minute):
    return datetime(2026, 10, 21, hour, minute, tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()


@pytest.mark.parametrize('delay_at', ['lock', 'generation'])
@pytest.mark.parametrize('start,end', [(stamp(22, 59), stamp(23, 1)),
                                     (stamp(23, 59), stamp(0, 4) + 86400)])
def test_scheduler_rechecks_time_after_waits(tmp_path, delay_at, start, end):
    async def case():
        cfg, store, brain, session, bot, dp = await setup(tmp_path)
        state = CompanionStore(cfg.database_path)
        await state.init()
        # Recent inbound permits only a night reminder, otherwise an idle event.
        inbound = start - (120 if start == stamp(23, 59) else 7 * 3600)
        await state.activity(1, 1, inbound, 'Asia/Tokyo', True)
        lock, entered, release = asyncio.Lock(), asyncio.Event(), asyncio.Event()
        clock = NS(value=start)
        fakebot = NS(send_message=AsyncMock())
        async def generate(*args, **kwargs):
            entered.set()
            await release.wait()
            return 'test'
        brain.proactive = AsyncMock(side_effect=generate)
        companion = Companion(cfg, state, store, brain, fakebot, {1: lock}, clock=lambda: clock.value)
        try:
            if delay_at == 'lock':
                await lock.acquire()
            task = asyncio.create_task(companion.tick())
            if delay_at == 'generation':
                await asyncio.wait_for(entered.wait(), 1)
            else:
                await asyncio.sleep(0.02)
            clock.value = end
            release.set()
            if lock.locked() and delay_at == 'lock':
                lock.release()
            await asyncio.wait_for(task, 1)
            fakebot.send_message.assert_not_awaited()
            if delay_at == 'lock':
                brain.proactive.assert_not_awaited()
        finally:
            release.set()
            await bot.session.close()
    asyncio.run(case())


def test_memories_paginate_without_losing_entries_or_other_user_data(tmp_path):
    async def case():
        cfg, store, brain, session, bot, dp = await setup(tmp_path)
        cfg.memory_limit = 100
        try:
            for n in range(34):
                await store.save_memories(1, [f'{n}-{i}:' + '喜😀' * 100 for i in range(3)], 100)
            await store.save_memories(2, ['PRIVATE_OTHER_USER'], 100)
            expected = await store.memories(1, 100)
            await feed(bot, dp, '/memories')
            pages = [c.text for c in session.calls if isinstance(c, SendMessage)]
            assert len(pages) > 1
            assert all(0 < len(page.encode('utf-16-le')) // 2 <= 3800 for page in pages)
            assert ''.join(pages) == '我记得这些：\n' + '\n'.join(f'{i}. {fact}' for i, fact in expected)
            assert 'PRIVATE_OTHER_USER' not in ''.join(pages)
        finally:
            await bot.session.close()
    asyncio.run(case())


def test_long_unbroken_unicode_text_is_split_without_loss():
    text = '😀' * 5000 + '中文' * 3000
    pages = list(text_pages(text))
    assert ''.join(pages) == text
    assert all(len(page.encode('utf-16-le')) // 2 <= 3800 for page in pages)


def test_text_retry_notice_matches_sdk_behavior():
    async def case():
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(429, json={'error': {'message': 'synthetic', 'code': 'rate_limit_exceeded'}},
                                  headers={'retry-after-ms': '1'})
        brain = Brain('fake', 'text', 'image', 'persona')
        await brain.client.close()
        brain.client = AsyncOpenAI(api_key='fake', max_retries=2,
                                   http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            with pytest.raises(RateLimitError) as caught:
                await brain.chat([], [], 'test')
            assert len(calls) == 3
            notice = error_message(caught.value)
            assert '不会继续重试' in notice and '没有自动' not in notice
            assert '没有自动重复' in error_message(caught.value, image=True)
        finally:
            await brain.client.close()
    asyncio.run(case())


@pytest.mark.parametrize('field,value', [('max_context_messages', -1), ('max_context_messages', 0),
                                        ('max_context_messages', 201), ('max_context_chars', -1),
                                        ('max_context_chars', 200001)])
def test_invalid_context_settings_fail_at_startup(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, telegram_bot_token='fake', openai_api_key='fake',
                 allowed_user_ids='1', **{field: value})
