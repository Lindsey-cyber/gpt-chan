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
