import asyncio
import logging

import httpx
import pytest
from openai import APIStatusError, BadRequestError, RateLimitError

from app.api_errors import error_details, error_message
from app.main import report_error
from test_image_intent import setup,feed
from app.ai import ImageRequest


def failure(status=400,code=None,**fields):
    body={'code':code,**fields}
    response=httpx.Response(status,request=httpx.Request('POST','https://api.openai.com/v1/images/edits'),headers={'x-request-id':'req_test'})
    cls=BadRequestError if status==400 else RateLimitError if status==429 else APIStatusError
    return cls('sensitive provider error: PRIVATE_PROMPT sk-secret',response=response,body=body)


@pytest.mark.parametrize('body,expected', [
    ({'code':'moderation_blocked','type':'image_generation_user_error'},'内容审核'),
    ({'code':'credit_balance_exhausted'},'余额'),
    ({'code':'project_spend_limit_exceeded'},'消费上限'),
    ({'code':'invalid_value','param':'quality'},'quality'),
    ({},'尚无足够信息'),
])
def test_400_causes_are_not_misreported_as_rate_limits(body,expected):
    text=error_message(failure(**body),image=True)
    assert expected in text
    assert '限速' not in text
    assert 'PRIVATE_PROMPT' not in text and 'sk-secret' not in text


@pytest.mark.parametrize('status,code,expected',[(429,'rate_limit_exceeded','限速'),(429,'insufficient_quota','余额'),
    (401,'invalid_api_key','密钥'),(403,'permission_denied','权限'),(404,'model_not_found','模型'),(503,'server_is_overloaded','服务')])
def test_distinct_auth_quota_throttle_and_server_errors(status,code,expected):
    assert expected in error_message(failure(status,code),image=True)


def test_safe_logs_keep_error_code_and_stage_without_private_body(caplog):
    exc=failure(code='moderation_blocked',type='image_generation_user_error',
        moderation_details={'moderation_stage':'output','categories':['PRIVATE_CATEGORY']},
        prompt='PRIVATE_PROMPT',api_key='sk-secret')
    with caplog.at_level(logging.ERROR): report_error('image_generation_failed',exc)
    assert 'code=moderation_blocked' in caplog.text and 'moderation_stage=output' in caplog.text
    assert 'req_test' in caplog.text
    assert all(word not in caplog.text for word in ('PRIVATE_PROMPT','PRIVATE_CATEGORY','sk-secret','sensitive provider error'))
    detail=error_details(failure(code='sk-secret',param='PRIVATE_PROMPT',type='PRIVATE_TYPE'))
    assert detail['code']==detail['param']==detail['api_type']=='other'
    wrapped=failure(error={'code':'moderation_blocked','moderation_details':{'moderation_stage':'input'}})
    assert error_details(wrapped)['moderation_stage']=='input'


def test_image_rejection_returns_specific_message_and_never_retries(tmp_path):
    async def case():
        cfg,store,brain,session,bot,dp=await setup(tmp_path)
        brain.chat.return_value=ImageRequest('成年白龙正常肖像',True)
        brain.image.side_effect=failure(code='moderation_blocked')
        await feed(bot,dp,'画一张自画像')
        assert brain.image.call_count==1
        assert (await store.image_task(1))[1]=='failed'
        assert any('内容审核' in (getattr(c,'text','') or '') for c in session.calls)
        await bot.session.close()
    asyncio.run(case())
