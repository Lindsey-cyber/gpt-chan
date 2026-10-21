from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Optional

import httpx
from app.image_errors import ImageOutcomeUnknown
from app.image_payload import MAX_ARCHIVE_BYTES, unpack_png

log = logging.getLogger(__name__)

# 固定角色标签：保证小白/白龙姬外貌一致（纯语言，不依赖参考图）
CHARACTER_PREFIX = (
    "masterpiece, best quality, absurdres, "
    "1girl, solo, adult woman, "
    "long white hair, silver white hair, pale purple eyes, "
    "white dragon horns, curved horns, "
    "white membrane wings, white dragon wings, "
    "white scaled tail, long tail, "
    "beautiful face, delicate features, "
)

CHARACTER_NEGATIVE = (
    "lowres, bad anatomy, bad hands, text, error, missing fingers, "
    "extra digit, fewer digits, cropped, worst quality, low quality, "
    "normal quality, jpeg artifacts, signature, watermark, username, "
    "blurry, child, loli, underage, baby, toddler"
)

# Keep the configured V4.5 model; never substitute another model automatically.
MODEL = "nai-diffusion-4-5-full"
TOTAL_TIMEOUT_SECONDS = 180.0


async def download_archive(token, payload):
    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/zip',
        'Accept-Encoding': 'identity',
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0, pool=10.0)) as client:
        async with client.stream('POST', 'https://image.novelai.net/ai/generate-image',
                                 json=payload, headers=headers) as response:
            if response.status_code >= 500 or response.status_code == 408:
                raise ImageOutcomeUnknown('NovelAI generation outcome unknown')
            if response.status_code >= 400:
                log.error('novelai_http status=%s', response.status_code)
                raise RuntimeError(f'NovelAI HTTP {response.status_code}')
            # Bound wire bytes, without implicit HTTP content decompression.
            if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                raise ValueError('Unexpected HTTP content encoding')
            length = response.headers.get('content-length')
            if length is not None and int(length) > MAX_ARCHIVE_BYTES:
                raise ValueError('Archive exceeds byte limit')
            data = bytearray()
            async for chunk in response.aiter_raw(chunk_size=64 * 1024):
                if len(data) + len(chunk) > MAX_ARCHIVE_BYTES:
                    raise ValueError('Archive exceeds byte limit')
                data.extend(chunk)
            return bytes(data)


async def generate(
    token: str,
    prompt: str,
    *,
    width: int = 832,
    height: int = 1216,
    steps: int = 28,
    scale: float = 5.5,
    seed: Optional[int] = None,
    self_portrait: bool = False,
) -> bytes:
    """
    调用 NovelAI Image API，返回 PNG bytes。
    token: Persistent API Token（pst-...）
    prompt: 场景/动作描述；仅 self_portrait 为真时添加角色前缀。
    """
    if not token or not token.startswith("pst-"):
        raise ValueError("NovelAI token missing or invalid (expect pst-...)")

    full_prompt = (CHARACTER_PREFIX if self_portrait else '') + prompt.strip()
    if seed is None:
        seed = secrets.randbelow(2**32)
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError('Seed must be an unsigned 32-bit integer')

    payload = {
        "input": full_prompt,
        "model": MODEL,
        "action": "generate",
        "parameters": {
            "width": width,
            "height": height,
            "scale": scale,
            "sampler": "k_euler_ancestral",
            "steps": steps,
            "n_samples": 1,
            "ucPreset": 0,
            "qualityToggle": True,
            "negative_prompt": CHARACTER_NEGATIVE,
            # V4/V4.5 use structured conditioning, documented by the provider:
            # https://image.novelai.net/docs/doc.json (image.V4ConditionInput)
            "v4_prompt": {
                "caption": {"base_caption": full_prompt, "char_captions": []},
                "use_coords": False,
                "use_order": True,
            },
            "v4_negative_prompt": {
                "caption": {"base_caption": CHARACTER_NEGATIVE, "char_captions": []},
                "use_coords": False,
                "use_order": True,
                "legacy_uc": False,
            },
            "image_format": "png",
            "seed": seed,
            "sm": False,
            "sm_dyn": False,
            "dynamic_thresholding": False,
            "cfg_rescale": 0,
            "noise_schedule": "native",
            "legacy": False,
            "legacy_v3_extend": False,
        },
    }
    try:
        # This deadline includes the complete response and off-thread validation.
        async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
            data = await download_archive(token, payload)
            png = await asyncio.to_thread(unpack_png, data)
    except (TimeoutError, httpx.TransportError) as exc:
        raise ImageOutcomeUnknown('NovelAI generation outcome unknown') from exc

    log.info("novelai_ok size=%d", len(png))
    return png
