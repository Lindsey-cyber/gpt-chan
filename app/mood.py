def next_mood(text: str, previous: str, remaining: int) -> tuple[str, int]:
    if any(x in text for x in ('别撒娇', '不要撒娇', '别卖萌', '正常说话', '别角色扮演', '不要角色扮演')):
        return 'plain', 3
    if any(x in text for x in ('认真一点', '认真检查', '严肃一点', '别傲娇', '仔细检查')):
        return 'attentive', 2
    if any(x in text for x in ('难过', '好累', '焦虑', '伤心', '崩溃', '不开心')):
        return 'caring', 3
    if any(x in text for x in ('摸摸头', '摸头', '揉揉头', '抱抱')):
        return 'soft-shy', 2
    if any(x in text for x in ('你好可爱', '你真可爱', '夸夸你', '你好棒', '你真棒', '喜欢你')):
        return 'bashful-proud', 3
    if any(x in text for x in ('你输了', '你输啦', '比一比', '挑战你')):
        return 'competitive-playful', 2
    if any(x in text for x in ('我成功了', '我做到了', '好消息')):
        return 'celebratory', 2
    if previous == 'plain' and remaining > 1:
        return previous, remaining - 1
    if any(x in text for x in ('报错', '代码', '怎么修', '解释一下', '原理', '教程')):
        return 'clever-playful', 1
    if remaining > 1:
        return previous, remaining - 1
    return 'steady', 0
