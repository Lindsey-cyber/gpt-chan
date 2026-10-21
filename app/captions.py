"""Short, non-explicit captions independent of the image backend."""
CAPTIONS = {
    'bailongji': (
        '主人，交卷啦！本天才这回也有好好发挥哦～',
        '这张收好呀，主人♡ 你的白龙姬正等着听评价呢。',
        '主人，给你看我的新作！现在可以稍微得意一下了吧～',
        '完成啦！主人看画，我就在旁边等一句夸奖♡',
    ),
    'xiaobai': (
        '主人，给你看～小白这回也有好好画，摸摸头嘛♡',
        '这张收好呀，主人。小白把一点小得意也塞进画里啦～',
        '主人，偷偷递给你♡ 你的小龙在旁边等夸呢。',
        '画完啦～小白把尾巴收好，凑过来陪主人一起看♡',
    ),
}

async def image_caption(store, uid, *, resend=False):
    if resend:
        return '主人，这张重新递给你♡ 用的是刚才存好的图片，没有重新画哦。'
    persona = await store.selected_persona(uid)
    index = await store.next_caption_index(uid)
    choices = CAPTIONS.get(persona,CAPTIONS['bailongji'])
    return choices[index % len(choices)]
