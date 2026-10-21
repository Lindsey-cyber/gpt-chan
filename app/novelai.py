from __future__ import annotations

import io
import logging
import zipfile
from typing import Optional

import httpx

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

# NovelAI V5 Full（支持 NSFW）
MODEL = "nai-diffusion-4-5-full"  # 若账号已有 V5 Full，可改为 nai-diffusion-5-full


async def generate(
    token: str,
    prompt: str,
    *,
    width: int = 832,
    height: int = 1216,
    steps: int = 28,
    scale: float = 5.5,
    seed: Optional[int] = None,
) -> bytes:
    """
    调用 NovelAI Image API，返回 PNG bytes。
    token: Persistent API Token（pst-...）
    prompt: 场景/动作描述（会自动加上角色前缀）
    """
    if not token or not token.startswith("pst-"):
        raise ValueError("NovelAI token missing or invalid (expect pst-...)")

    full_prompt = CHARACTER_PREFIX + prompt.strip()
    if "nsfw" not in full_prompt.lower():
        full_prompt = "nsfw, rating:explicit, " + full_prompt

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
            "sm": False,
            "sm_dyn": False,
            "dynamic_thresholding": False,
            "cfg_rescale": 0,
            "noise_schedule": "native",
            "legacy": False,
            "legacy_v3_extend": False,
        },
    }
    if seed is not None:
        payload["parameters"]["seed"] = seed

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/zip",
    }

    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            "https://image.novelai.net/ai/generate-image",
            json=payload,
            headers=headers,
        )
        if resp.status_code == 401:
            raise RuntimeError("NovelAI auth failed (check token)")
        if resp.status_code == 402:
            raise RuntimeError("NovelAI Anlas insufficient")
        if resp.status_code >= 400:
            # 不把响应体写进日志，避免敏感内容
            log.error("novelai_http status=%s", resp.status_code)
            raise RuntimeError(f"NovelAI HTTP {resp.status_code}")

        data = resp.content

    # 返回的是 zip，里面是一张 PNG
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".png")]
        if not names:
            raise ValueError("NovelAI zip contained no PNG")
        png = zf.read(names[0])

    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("NovelAI returned non-PNG data")

    log.info("novelai_ok size=%d", len(png))
    return png
