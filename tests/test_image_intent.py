from app.main import requested_image, image_followup

def test_image_intent():
    for prompt in ('帮我画一张白龙娘头像', '生成一张图片', '画一个你自己的自画像', '画一个自己的Q版头像', '画一张风景', '给我画个你'):
        assert requested_image(prompt), prompt
    for prompt in ('你会画画吗', '解释一下图片生成的原理', '画图呢', '图片呢', '画好了吗'):
        assert not requested_image(prompt), prompt

def test_image_followup():
    assert image_followup('画图呢')
    assert image_followup('宝宝 图片呢')
    assert not image_followup('画一个你自己的自画像')
