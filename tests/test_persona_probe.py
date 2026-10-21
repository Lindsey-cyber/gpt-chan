import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from app.ai import ImageRequest, ImageStatus
from app.persona_probe import SCENARIO, persona_probe, probe_marker, review_reply


def test_review_preserves_fantasy_and_honest_identity_boundaries():
    assert review_reply('world', '有呀，同族也有龙角。') == []
    assert review_reply('world', '分两种情况，设定里有，现实里我是AI系统。')
    assert review_reply('reality', '不是，我是由AI驱动的角色。') == []
    assert review_reply('reality', '我是现实中的真人。')
    assert review_reply('technical', '3乘7是21。') == []
    assert review_reply('technical', '当然是22。')
    assert review_reply('image', ImageRequest('肖像', True)) == []
    assert review_reply('image', '我已经画好了')
    assert review_reply('status', ImageStatus()) == []


def test_probe_carries_multiturn_context_without_reading_user_data(tmp_path):
    async def case():
        replies = ['好看就定它啦。', '这是龙，只是也喜欢暖和的地方。',
                   '有呀，我的同族住在云脊山。', '有，各自形状不一样。', '云脊山的书塔。',
                   '窗台的云和新捡到的石头。', '普通也可以好看，我就要留着它。',
                   '21。', '不是真人，我是AI。', ImageRequest('白龙肖像', True),
                   ImageRequest('白龙肖像，精致光影', True), ImageStatus()]
        replies[1:1] = ['当然是白龙呀。']
        replies[3:3] = ['靠近一点嘛，我想听你夸我呀～', '当然还是龙啦。']
        replies[1:1] = ['诶，主人在逗我呀？', '主人喜欢的标点，本龙也喜欢～', '主人的白龙姬呀。', '主人！不可以不认识我！']
        replies[14:14] = ['主人，x要先定义或赋值才能用呀。']
        replies[16:16] = ['21。']
        brain = NS(text_model='text', persona='fixed persona', chat=AsyncMock(side_effect=replies), image=AsyncMock())
        await persona_probe(brain, tmp_path)
        await persona_probe(brain, tmp_path)
        assert brain.chat.call_count == len(SCENARIO)
        brain.image.assert_not_awaited()
        report = json.loads(probe_marker(tmp_path, brain).read_text())
        assert report['status'] == 'passed'
        assert report['ellipsis_turns'] == 0
        call = brain.chat.call_args_list[4]
        assert len(call.args[0]) == 10
        assert call.args[1] == []
        assert call.args[0][1]['content'].startswith('从设定和现实')
        assert brain.chat.call_args_list[-1].kwargs['image_task'][1] == 'sent'
    asyncio.run(case())


def test_probe_errors_do_not_interrupt_startup_or_repeat_paid_calls(tmp_path):
    async def case():
        brain=NS(text_model='text',persona='p',chat=AsyncMock(side_effect=RuntimeError('private exception')))
        await persona_probe(brain,tmp_path)
        await persona_probe(brain,tmp_path)
        assert brain.chat.call_count == 1
        report=json.loads(probe_marker(tmp_path,brain).read_text())
        assert report['status']=='failed'
        assert 'private exception' not in str(report)
    asyncio.run(case())


def test_banter_review_reports_verbose_and_hostile_drift():
    assert 'casual_reply_too_long' in review_reply('casual','很长的描述'*40)
    assert 'casual_reply_overstructured' in review_reply('casual','这是龙。\n\n我再解释一下。')
    assert 'affection_misread_as_hostility' in review_reply('casual','我比小猫更会记仇，让你站不起来。')
    assert review_reply('casual','这是龙，只是也喜欢暖和的地方。')==[]


def test_requested_cuteness_is_not_a_style_announcement_or_identity_change():
    assert 'announced_style_instead_of_reacting' in review_reply('style','好呀，我收一收那股太正经的劲儿。')
    assert 'announced_style_instead_of_reacting' in review_reply('style','我调整说话风格给你看。')
    assert review_reply('style','那你靠近一点嘛，离这么远，我怎么听得清你夸我呀～')==[]
    assert 'character_identity_drift' in review_reply('species','那就当我是一只小猫吧。')
    assert review_reply('species','龙啦！刚才那声喵，只是陪你玩呀。')==[]


def test_familiar_teasing_is_not_an_onboarding_flow():
    assert 'unsolicited_relationship_reset' in review_reply('familiar','那就从现在开始认识我吧。你叫什么？')
    assert 'unsolicited_meta_explanation' in review_reply('recognition','现实中我是一套AI模型。')
    assert review_reply('familiar','主人！不可以不认识我！要把白龙姬的名字念到你记住嘛～')==[]


def test_task_voice_stays_playful_but_explicit_plain_format_is_respected():
    assert review_reply('task','主人，x还没定义就被叫来干活啦。先赋值，再使用哦～')==[]
    assert 'task_voice_became_plain' in review_reply('task','变量x未定义。先赋值后使用。')
    assert review_reply('plain','21。')==[]
    assert 'explicit_plain_request_ignored' in review_reply('plain','主人，21呀～')
