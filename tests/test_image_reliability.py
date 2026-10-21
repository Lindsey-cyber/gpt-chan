import asyncio
import base64
import io
import struct
import threading
import zipfile
import zlib
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from aiogram.methods import SendMessage, SendPhoto
from PIL import Image
from openai import APITimeoutError

from app import image_payload, novelai
from app.ai import Brain, ImageRequest, ImageStatus
from app.image_errors import ImageOutcomeUnknown
from app.image_payload import unpack_png, validate_png
from app.storage import Storage
from test_image_intent import setup, feed, png


def archive(data=None, *, names=('image.png',)):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as result:
        for name in names:
            result.writestr(name, png() if data is None else data)
    return out.getvalue()


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks, delay=0):
        self.chunks, self.delay = chunks, delay
        self.reads, self.closed = 0, False

    async def __aiter__(self):
        for chunk in self.chunks:
            await asyncio.sleep(self.delay)
            self.reads += 1
            yield chunk

    async def aclose(self):
        self.closed = True


def transport(monkeypatch, handler):
    client_type = httpx.AsyncClient
    monkeypatch.setattr(novelai.httpx, 'AsyncClient',
                        lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs))


def test_valid_archive_and_png():
    assert unpack_png(archive()) == png()


def test_zip_bomb_rejected_before_image_decoding(monkeypatch):
    bomb = archive(b'\x89PNG\r\n\x1a\n' + b'\0' * (8 * 1024 * 1024))
    assert len(bomb) < 10 * 1024
    with patch.object(image_payload, 'validate_png') as decode:
        with pytest.raises(ValueError, match='compression ratio'):
            unpack_png(bomb)
        decode.assert_not_called()


@pytest.mark.parametrize('names', [(), ('a.png', 'b.png'), tuple(f'{i}.txt' for i in range(9))])
def test_archive_entry_limits(names):
    with pytest.raises(ValueError):
        unpack_png(archive(names=names))


def test_archive_and_member_byte_limits(monkeypatch):
    data = archive()
    monkeypatch.setattr(image_payload, 'MAX_ARCHIVE_BYTES', len(data) - 1)
    with pytest.raises(ValueError, match='Archive exceeds byte'):
        unpack_png(data)
    monkeypatch.setattr(image_payload, 'MAX_ARCHIVE_BYTES', len(data))
    monkeypatch.setattr(image_payload, 'MAX_PNG_BYTES', len(png()) - 1)
    with pytest.raises(ValueError, match='Image exceeds byte'):
        unpack_png(data)
    monkeypatch.setattr(image_payload, 'MAX_ARCHIVE_EXPANDED_BYTES', 1)
    with pytest.raises(ValueError, match='expanded byte'):
        unpack_png(data)


def test_actual_member_read_is_bounded(monkeypatch):
    monkeypatch.setattr(image_payload, 'MAX_PNG_BYTES', 4)
    member = io.BytesIO(b'x' * 100)
    data = archive(b'x')
    with patch.object(zipfile.ZipFile, 'open', return_value=member):
        with pytest.raises(ValueError, match='Image exceeds byte'):
            unpack_png(data)


@pytest.mark.parametrize('data', [b'\x89PNG\r\n\x1a\n' + b'not an image', png()[:-8]])
def test_corrupt_images_rejected(data):
    with pytest.raises((ValueError, OSError, SyntaxError)):
        unpack_png(archive(data))


def test_bad_pixel_data_with_valid_chunk_checksums_is_rejected():
    def chunk(kind, value):
        return (struct.pack('!I', len(value)) + kind + value
                + struct.pack('!I', zlib.crc32(kind + value)))
    data = (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('!IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', b'not zlib data') + chunk(b'IEND', b''))
    with pytest.raises(OSError):
        validate_png(data)


@pytest.mark.parametrize('size', [(4097, 1), (2049, 2049)])
def test_pixel_limits_checked_before_loading(size):
    out = io.BytesIO()
    Image.new('RGB', size).save(out, 'PNG')
    with patch.object(Image.Image, 'load', side_effect=AssertionError('must not decode')):
        with pytest.raises(ValueError, match='pixel limit'):
            validate_png(out.getvalue())


@pytest.mark.parametrize('declared_length', [False, True])
def test_stream_download_limit_and_cleanup(monkeypatch, declared_length):
    async def case():
        stream = Stream([b'x' * 65536] * 4)
        monkeypatch.setattr(novelai, 'MAX_ARCHIVE_BYTES', 65536)
        headers = {'content-length': str(4 * 65536)} if declared_length else {}
        transport(monkeypatch, lambda request: httpx.Response(200, stream=stream, headers=headers))
        with pytest.raises(ValueError, match='byte limit'):
            await novelai.generate('pst-test', 'test')
        assert stream.closed
        assert stream.reads == (0 if declared_length else 2)
    asyncio.run(case())


def test_successful_stream_calls_provider_once(monkeypatch):
    async def case():
        calls = []
        stream = Stream([archive()])
        def handler(request):
            calls.append(request)
            assert request.headers['accept-encoding'] == 'identity'
            return httpx.Response(200, stream=stream)
        transport(monkeypatch, handler)
        assert await novelai.generate('pst-test', 'test') == png()
        assert len(calls) == 1 and stream.closed
    asyncio.run(case())


def test_trickling_response_hits_total_deadline_and_closes(monkeypatch):
    async def case():
        calls = []
        stream = Stream([b'x'] * 1000, delay=0.01)
        def handler(request):
            calls.append(request)
            return httpx.Response(200, stream=stream)
        transport(monkeypatch, handler)
        monkeypatch.setattr(novelai, 'TOTAL_TIMEOUT_SECONDS', 0.05)
        with pytest.raises(ImageOutcomeUnknown):
            await asyncio.wait_for(novelai.generate('pst-test', 'test'), 1)
        assert stream.closed and stream.reads < 1000 and len(calls) == 1
    asyncio.run(case())


def test_validation_runs_off_thread_and_is_covered_by_deadline(monkeypatch):
    async def case():
        release = threading.Event()
        worker = []
        def slow_validation(data):
            worker.append(threading.get_ident())
            release.wait(2)
            return png()
        transport(monkeypatch, lambda request: httpx.Response(200, stream=Stream([archive()])))
        monkeypatch.setattr(novelai, 'unpack_png', slow_validation)
        monkeypatch.setattr(novelai, 'TOTAL_TIMEOUT_SECONDS', 0.5)
        try:
            with pytest.raises(ImageOutcomeUnknown):
                await asyncio.wait_for(novelai.generate('pst-test', 'test'), 1)
            assert worker and worker[0] != threading.get_ident()
        finally:
            release.set()
    asyncio.run(case())


@pytest.mark.parametrize('status,unknown', [(401, False), (402, False), (429, False), (408, True), (503, True)])
def test_http_failures_do_not_read_body_or_retry(monkeypatch, status, unknown):
    async def case():
        stream = Stream([b'private error body'])
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(status, stream=stream)
        transport(monkeypatch, handler)
        with pytest.raises(ImageOutcomeUnknown if unknown else RuntimeError):
            await novelai.generate('pst-test', 'test')
        assert len(calls) == 1 and stream.reads == 0 and stream.closed
    asyncio.run(case())


def test_unexpected_http_compression_rejected_before_reading(monkeypatch):
    async def case():
        stream = Stream([b'compressed body'])
        transport(monkeypatch, lambda request: httpx.Response(
            200, headers={'content-encoding': 'gzip'}, stream=stream))
        with pytest.raises(ValueError, match='content encoding'):
            await novelai.generate('pst-test', 'test')
        assert stream.reads == 0 and stream.closed
    asyncio.run(case())


def test_openai_path_rejects_fake_png():
    async def case():
        brain = Brain('fake', 'text', 'image', 'persona')
        result = NS(data=[NS(b64_json=base64.b64encode(b'\x89PNG\r\n\x1a\ninvalid').decode())])
        client = NS(images=NS(generate=AsyncMock(return_value=result)))
        try:
            with patch.object(brain.client, 'with_options', return_value=client):
                with pytest.raises((ValueError, OSError)):
                    await brain.image('test')
        finally:
            await brain.client.close()
    asyncio.run(case())


@pytest.mark.parametrize('failure', [TimeoutError(), ImageOutcomeUnknown(),
                                  httpx.ReadError('synthetic'),
                                  APITimeoutError(request=httpx.Request('POST', 'https://example.com'))])
def test_uncertain_generation_persists_and_warns_without_replay(tmp_path, failure):
    async def case():
        cfg, store, brain, session, bot, dp = await setup(tmp_path)
        brain.chat.return_value = ImageRequest('test', False)
        brain.image.side_effect = failure
        try:
            await feed(bot, dp, '画一张图')
            assert (await store.image_task(1))[1] == 'unknown'
            await Storage(store.path).init()
            assert (await store.image_task(1))[1] == 'unknown'
            answers = [c.text for c in session.calls if isinstance(c, SendMessage)]
            assert any('可能再次扣费' in text for text in answers)
            brain.chat.side_effect = [ImageStatus(), '结果及扣费未知']
            await feed(bot, dp, '图片呢', number=2)
            assert brain.image.call_count == 1
            assert not any(isinstance(c, SendPhoto) for c in session.calls)
        finally:
            await bot.session.close()
    asyncio.run(case())


def test_cancelled_generation_preserves_unknown_and_releases_slot(tmp_path):
    async def case():
        cfg, store, brain, session, bot, dp = await setup(tmp_path)
        entered = asyncio.Event()
        async def waiting(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        brain.chat.return_value = ImageRequest('test', False)
        brain.image.side_effect = waiting
        task = asyncio.create_task(feed(bot, dp, '画一张图'))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert (await store.image_task(1))[1] == 'unknown'
            brain.image.side_effect = None
            brain.image.return_value = png()
            await asyncio.wait_for(feed(bot, dp, '画一张图', uid=2, number=2), 1)
            assert (await store.image_task(2))[1] == 'sent'
        finally:
            task.cancel()
            await bot.session.close()
    asyncio.run(case())
