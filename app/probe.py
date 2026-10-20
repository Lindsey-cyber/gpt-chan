"""Explicit opt-in, one-time API verification. Never send synthetic user messages."""
import json
import logging
from pathlib import Path

from app.ai import ImageRequest

log = logging.getLogger(__name__)

async def startup_probe(brain, data_dir, reference):
    marker = Path(data_dir) / 'api-probe-v1.json'
    try:
        with marker.open('x') as out:
            json.dump({'status': 'attempted'}, out)
    except FileExistsError:
        return
    try:
        text = await brain.chat([], [], '请用一句话打招呼。')
        if not isinstance(text, str) or not text:
            raise ValueError('Text probe returned no greeting')
        log.info('api_probe_text_ok model=%s', brain.text_model)
        first = await brain.chat([], [], '画一张你的自画像')
        if not isinstance(first, ImageRequest) or not first.self_portrait:
            raise ValueError('Portrait routing probe failed')
        for followup in ('精致一点', '继续出图'):
            plan = await brain.chat([{'role': 'user', 'content': '画一张你的自画像'},
                                    {'role': 'assistant', 'content': '[图片已发送]'}], [], followup,
                                    image_task=(first.prompt, 'sent'))
            if not isinstance(plan, ImageRequest) or not plan.self_portrait:
                raise ValueError('Image follow-up probe failed')
        log.info('api_probe_tool_routing_ok')
        data = await brain.image('以参考图中同一个白龙少女为人物，保持脸、银白长发、龙角、翅膀、尾巴、白色长裙与饰品一致。绘制单幅半身肖像，不要三视图或文字。', str(reference), quality='medium')
        (Path(data_dir) / 'api-probe-portrait.png').write_bytes(data)
        marker.write_text(json.dumps({'status': 'passed', 'image_bytes': len(data), 'text_model': brain.text_model, 'image_model': brain.image_model}))
        log.info('api_probe_image_edit_ok bytes=%d; telegram_delivery_not_tested', len(data))
    except Exception as exc:
        marker.write_text(json.dumps({'status': 'failed', 'error_type': type(exc).__name__}))
        log.error('api_probe_failed type=%s status=%s request_id=%s', type(exc).__name__, getattr(exc,'status_code',None), getattr(exc,'request_id',None))
