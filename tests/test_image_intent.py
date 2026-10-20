from app.main import requested_image

def test_image_intent():
    assert requested_image('帮我画一张白龙娘头像')
    assert requested_image('生成一张图片')
    assert not requested_image('你会画画吗')
    assert not requested_image('解释一下图片生成的原理')
