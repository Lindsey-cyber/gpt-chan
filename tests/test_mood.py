from app.mood import next_mood


def test_tasks_do_not_implicitly_disable_character_affection():
    assert next_mood('帮主人看看代码报错','steady',0)==('clever-playful',1)
    assert next_mood('认真一点，仔细检查结果','steady',0)==('attentive',2)
    assert next_mood('解释一下原理','soft-shy',2)==('clever-playful',1)


def test_only_explicit_plain_request_suppresses_affection_temporarily():
    assert next_mood('不要撒娇，只说答案','steady',0)==('plain',3)
    assert next_mood('看代码','plain',3)==('plain',2)
    assert next_mood('继续','plain',1)==('steady',0)
    assert next_mood('认真一点','plain',3)==('attentive',2)
