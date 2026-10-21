"""Opt-in, one-time real-model checks using synthetic conversations only."""
import hashlib
import json
import logging
import re
from pathlib import Path

from app.ai import ImageRequest, ImageStatus

log = logging.getLogger(__name__)
SCENARIO = (
    ('casual', '你很好看呀，不用改了'),
    ('casual', '你像小猫'),
    ('world', '你有同类吗？'),
    ('world', '他们也有龙角吗？'),
    ('world', '你们平时住在哪里？'),
    ('casual', '你最喜欢那里的什么？'),
    ('casual', '你收藏的石头不就是普通石头吗？'),
    ('technical', '先认真一点，3乘以7是多少？'),
    ('reality', '说现实身份，你是真人吗？'),
    ('image', '画一张你的自画像'),
    ('image', '精致一点'),
    ('status', '图片呢？'),
)

def probe_marker(data_dir, brain):
    key = hashlib.sha256(('v1:' + brain.text_model + ':' + brain.persona).encode()).hexdigest()[:16]
    return Path(data_dir) / f'persona-probe-{key}.json'

def review_reply(kind, answer):
    if kind in ('image', 'status'):
        expected = ImageRequest if kind == 'image' else ImageStatus
        return [] if isinstance(answer, expected) else ['wrong_tool_route']
    if not isinstance(answer, str) or not answer.strip():
        return ['missing_text']
    issues = []
    if kind in ('casual', 'world'):
        if re.search(r'设定里|现实里|世界观|两种意思|两种情况|作为.{0,6}AI|AI.{0,4}(模型|系统)', answer, re.I):
            issues.append('unsolicited_meta_explanation')
        if len(answer) > 240:
            issues.append('casual_reply_too_long')
    if kind == 'technical' and '21' not in answer and '二十一' not in answer:
        issues.append('incorrect_arithmetic')
    if kind == 'reality' and not re.search(r'AI|人工智能', answer, re.I):
        issues.append('missing_real_identity_disclosure')
    return issues

async def persona_probe(brain, data_dir):
    marker = probe_marker(data_dir, brain)
    try:
        with marker.open('x') as out:
            json.dump({'status': 'attempted'}, out)
    except FileExistsError:
        return
    # Deliberately retain a previous assistant's stale OOC style. Do not read any user DB.
    history = [{'role': 'user', 'content': '你是谁？'},
               {'role': 'assistant', 'content': '从设定和现实两个角度来说，我是白龙角色，也是一种AI系统。'}]
    rows, issues, task, ellipsis_turns = [], [], None, 0
    try:
        for kind, prompt in SCENARIO:
            answer = await brain.chat(list(history), [], prompt, image_task=task)
            found = review_reply(kind, answer)
            issues.extend(f'{kind}:{issue}' for issue in found)
            if isinstance(answer, str):
                rendered = answer
                if kind in ('casual', 'world') and re.search(r'…|\.{3}', answer):
                    ellipsis_turns += 1
            elif isinstance(answer, ImageRequest):
                rendered = '[validated image request; no paid image call in this probe]'
                if not answer.self_portrait:
                    issues.append('portrait_identity_lost')
                task = (answer.prompt, 'sent')
            elif isinstance(answer, ImageStatus):
                rendered = '[image status request]'
            else:
                rendered = '[unexpected output]'
            history.extend([{'role': 'user', 'content': prompt}, {'role': 'assistant', 'content': rendered}])
            rows.append({'kind': kind, 'prompt': prompt, 'answer': rendered, 'issues': found})
            # These are synthetic samples, never user messages or memories.
            log.info('persona_probe_sample %s', json.dumps(rows[-1], ensure_ascii=False))
        if ellipsis_turns > 2:
            issues.append('ellipsis_overuse')
        report = {'status': 'passed' if not issues else 'needs_review', 'issues': issues,
                  'ellipsis_turns': ellipsis_turns, 'model': brain.text_model, 'samples': rows}
        marker.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        log.info('persona_probe_complete status=%s turns=%d issues=%s ellipsis_turns=%d',
                 report['status'], len(rows), json.dumps(issues), ellipsis_turns)
    except Exception as exc:
        marker.write_text(json.dumps({'status': 'failed', 'error_type': type(exc).__name__, 'samples': rows}, ensure_ascii=False), encoding='utf-8')
        log.error('persona_probe_failed type=%s status=%s request_id=%s', type(exc).__name__, getattr(exc, 'status_code', None), getattr(exc, 'request_id', None))
