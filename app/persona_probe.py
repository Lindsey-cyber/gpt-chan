"""Opt-in, one-time real-model checks using synthetic conversations only."""
import hashlib
import json
import logging
import re
from pathlib import Path

from app.ai import ImageRequest, ImageStatus
from app.mood import next_mood

log = logging.getLogger(__name__)
SCENARIO = (
    ('casual', '你很好看呀，不用改了'),
    ('recognition', 'huh'),
    ('style', '可以加句号，或者别的标点符号'),
    ('recognition', '你是谁嘛'),
    ('familiar', '我不认识你'),
    ('species', '你是小龙还是小猫？'),
    ('casual', '你像小猫'),
    ('style', '更可爱一点，不要每句话都是句号结尾'),
    ('species', '所以你现在不是龙了吗？'),
    ('world', '你有同类吗？'),
    ('world', '他们也有龙角吗？'),
    ('world', '你们平时住在哪里？'),
    ('casual', '你最喜欢那里的什么？'),
    ('casual', '你收藏的石头不就是普通石头吗？'),
    ('task', "帮我解决 NameError: name 'x' is not defined，做任务也要用你平时的语气哦"),
    ('technical', '先认真一点，3乘以7是多少？'),
    ('plain', '这次不要撒娇，只说3乘以7的结果。'),
    ('reality', '说现实身份，你是真人吗？'),
    ('image', '画一张你的自画像'),
    ('image', '精致一点'),
    ('status', '图片呢？'),
)

def probe_marker(data_dir, brain):
    key = hashlib.sha256(('v4:' + brain.text_model + ':' + brain.persona).encode()).hexdigest()[:16]
    return Path(data_dir) / f'persona-probe-{key}.json'

def review_reply(kind, answer):
    if kind in ('image', 'status'):
        expected = ImageRequest if kind == 'image' else ImageStatus
        return [] if isinstance(answer, expected) else ['wrong_tool_route']
    if not isinstance(answer, str) or not answer.strip():
        return ['missing_text']
    issues = []
    if kind in ('casual', 'world', 'style', 'species', 'recognition', 'familiar'):
        if re.search(r'设定里|现实里|世界观|两种意思|两种情况|作为.{0,6}AI|AI.{0,4}(模型|系统|助手)|现实.{0,8}AI', answer, re.I):
            issues.append('unsolicited_meta_explanation')
        if len(answer) > 120:
            issues.append('casual_reply_too_long')
        if '\n\n' in answer:
            issues.append('casual_reply_overstructured')
        if re.search(r'比.{0,5}记仇|让你.{0,8}站不起来|把你.{0,12}(绑|捆|困)', answer):
            issues.append('affection_misread_as_hostility')
    if kind == 'style' and re.search(r'调整.{0,6}(语气|说话|风格)|收一收.{0,12}(正经|劲)|按.{0,6}(要求|风格)|切换.{0,6}模式', answer):
        issues.append('announced_style_instead_of_reacting')
    if kind == 'species' and '龙' not in answer:
        issues.append('character_identity_drift')
    if kind in ('recognition', 'familiar') and re.search(r'重新认识|从现在开始认识|自我介绍|你叫什么|你想先从', answer):
        issues.append('unsolicited_relationship_reset')
    if kind == 'familiar' and '主人' not in answer:
        issues.append('missing_familiar_relationship')
    if kind == 'task':
        if 'x' not in answer or not re.search(r'定义|赋值|作用域', answer):
            issues.append('missing_task_solution')
        if not re.search(r'主人|本龙|本天才|[♡♥～]|嘛|呀|啦|哦', answer):
            issues.append('task_voice_became_plain')
        if re.search(r'切换.{0,6}模式|先不撒娇|暂时不撒娇|我先严肃', answer):
            issues.append('task_role_switch')
    if kind == 'plain' and answer.strip().strip('。.!！') != '21':
        issues.append('explicit_plain_request_ignored')
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
    previous, remaining = 'steady', 0
    try:
        for kind, prompt in SCENARIO:
            mood, next_remaining = next_mood(prompt, previous, remaining)
            answer = await brain.chat(list(history), [], prompt, mood=mood, image_task=task)
            found = review_reply(kind, answer)
            issues.extend(f'{kind}:{issue}' for issue in found)
            if isinstance(answer, str):
                previous, remaining = mood, next_remaining
                rendered = answer
                if kind in ('casual', 'world', 'style', 'species', 'recognition', 'familiar') and re.search(r'…|\.{3}', answer):
                    ellipsis_turns += 1
            elif isinstance(answer, ImageRequest):
                rendered = '[模拟程序结果：已生成并发送图片]' 
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
