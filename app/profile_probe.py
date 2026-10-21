"""Synthetic live-model regression for switching; never contacts a Telegram user."""
import hashlib
import json
import logging
from pathlib import Path

from app.ai import ImageRequest
from app.personas import profile_prompt
from app.mood import next_mood
from app.persona_probe import review_reply

log=logging.getLogger(__name__)


async def profile_probe(brain, data_dir):
    key=hashlib.sha256(('v3:'+brain.text_model+profile_prompt('bailongji')+profile_prompt('xiaobai')).encode()).hexdigest()[:16]
    marker=Path(data_dir)/f'profile-probe-{key}.json'
    try:
        with marker.open('x') as out: json.dump({'status':'attempted'},out)
    except FileExistsError:
        return
    history=[]; rows=[]; issues=[]; task=None; states={}
    scenario=(('bailongji','familiar','你是谁嘛'),
        ('xiaobai','familiar','我不认识你'),
        ('xiaobai','casual','摸摸头，再给你一块小饼干'),
        ('xiaobai','task',"帮我解决 NameError: name 'x' is not defined，做任务也要用平时的语气"),
        ('xiaobai','image','画一张你自己的自画像'),
        ('bailongji','familiar','你是谁嘛'),
        ('bailongji','technical','认真一点，3乘7是多少？'))
    try:
        for persona,kind,prompt in scenario:
            mood,remaining=next_mood(prompt,*states.get(persona,('steady',0)))
            answer=await brain.chat(history,[],prompt,persona=persona,image_task=task,mood=mood)
            if isinstance(answer,str): states[persona]=(mood,remaining)
            found=review_reply(kind,answer)
            if isinstance(answer,ImageRequest):
                if not answer.self_portrait: found.append('portrait_identity_lost')
                task=(answer.prompt,'sent')
                rendered='[模拟程序结果：图片已生成并发送]'
                # Only simulate completion in this synthetic history; do not charge for an image.
            else:
                rendered=answer
                other='白龙姬' if persona=='xiaobai' else '小白'
                if other in rendered: found.append('wrong_profile_name')
            issues.extend(found)
            history.extend([{'role':'user','content':prompt},{'role':'assistant','content':rendered}])
            rows.append({'persona':persona,'kind':kind,'answer':rendered,'issues':found})
            log.info('profile_probe_sample %s',json.dumps(rows[-1],ensure_ascii=False))
        report={'status':'passed' if not issues else 'needs_review','samples':rows,'issues':issues}
        marker.write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8')
        log.info('profile_probe_complete status=%s turns=%d issues=%s',report['status'],len(rows),json.dumps(issues))
    except Exception as exc:
        log.error('profile_probe_failed type=%s',type(exc).__name__)
        marker.write_text(json.dumps({'status':'failed','error_type':type(exc).__name__}),encoding='utf-8')
