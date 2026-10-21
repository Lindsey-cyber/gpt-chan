"""Classify API failures without retaining error messages, prompts, or credentials."""
from openai import APIStatusError, APITimeoutError, RateLimitError

CODES = frozenset(('moderation_blocked','content_policy_violation','invalid_image_format',
    'invalid_image','invalid_image_url','image_parse_error','invalid_base64_image',
    'invalid_request_error','invalid_value','unsupported_value','unknown_parameter',
    'invalid_parameter','unsupported_parameter','model_not_found','permission_denied',
    'invalid_api_key','insufficient_quota','credit_balance_exhausted',
    'project_spend_limit_exceeded','organization_spend_limit_exceeded',
    'organization_usage_limit_exceeded','rate_limit_exceeded','slow_down',
    'server_is_overloaded','billing_hard_limit_reached','billing_not_active',
    'billing_limit_reached','invalid_image_size','image_too_large'))
TYPES = frozenset(('image_generation_user_error','invalid_request_error','rate_limit_error',
    'insufficient_quota','authentication_error','permission_error','server_error','not_found_error'))
PARAMS = frozenset(('model','image','prompt','size','quality','n','output_format',
    'output_compression','background','moderation','input_fidelity','service_tier','user'))
QUOTA = frozenset(('insufficient_quota','credit_balance_exhausted','project_spend_limit_exceeded',
    'organization_spend_limit_exceeded','organization_usage_limit_exceeded',
    'billing_hard_limit_reached','billing_not_active','billing_limit_reached'))


def error_details(exc):
    body = getattr(exc,'body',None)
    body = body if isinstance(body,dict) else {}
    if isinstance(body.get('error'),dict): body = body['error']
    def field(name, allowed):
        value = getattr(exc,name,None) or body.get(name)
        return value if isinstance(value,str) and value in allowed else ('other' if value is not None else 'missing')
    moderation = body.get('moderation_details')
    stage = moderation.get('moderation_stage') if isinstance(moderation,dict) else None
    return {'code':field('code',CODES),'api_type':field('type',TYPES),
        'param':field('param',PARAMS),
        'moderation_stage':stage if stage in ('input','output','unknown') else 'missing'}


def error_message(exc, *, image=False):
    detail = error_details(exc)
    code, status = detail['code'], getattr(exc,'status_code',None)
    subject = '绘图请求' if image else '请求'
    if code in ('moderation_blocked','content_policy_violation'):
        return '主人，这次请求没有通过接口的内容审核。我没有自动重试；改一下要求后再试吧。'
    if code in QUOTA or detail['api_type']=='insufficient_quota':
        return '主人，接口返回余额或费用额度不足，需要检查账户余额和消费上限；重复请求暂时没用。'
    if isinstance(exc,RateLimitError) or status==429:
        return '主人，接口暂时限速了，稍等一会儿再试。我没有自动重复这次请求。'
    if isinstance(exc,APITimeoutError):
        return '这次等待超时了，结果暂时无法确认。我没有自动重试；需要的话请明确说再试一次。'
    if isinstance(exc,APIStatusError):
        if status==401:
            return '主人，接口凭据验证失败，需要检查API密钥；这次没有自动重试。'
        if status==403:
            return '主人，接口拒绝了访问权限，需要检查账户或模型权限；这次没有自动重试。'
        if status==404 or code=='model_not_found':
            return '主人，接口找不到当前模型或资源，需要检查配置；这次没有自动重试。'
        if status==400:
            if detail['param'] not in ('missing','other'):
                return f"主人，这次{subject}的参数被接口拒绝了（{detail['param']}），需要检查请求格式；我没有自动重试。"
            return f'主人，这次{subject}被接口拒绝了（HTTP 400），尚无足够信息确认具体原因。我没有自动重试。'
        if status and status>=500:
            return '主人，接口服务暂时出错了，稍后再试吧。这次结果未确认，我没有自动重复请求。'
    return '这次没能完成，稍后再试一次吧。'
