"""Split plain text conservatively by Telegram's UTF-16 length budget."""
MAX_TEXT_UNITS = 3800


def text_pages(text, limit=MAX_TEXT_UNITS):
    page, units = [], 0
    for line in text.splitlines(keepends=True):
        line_units = len(line.encode('utf-16-le')) // 2
        if page and units + line_units > limit:
            yield ''.join(page)
            page, units = [], 0
        for char in line:
            width = 2 if ord(char) > 0xffff else 1
            if units + width > limit:
                yield ''.join(page)
                page, units = [], 0
            page.append(char)
            units += width
    if page:
        yield ''.join(page)


async def answer_text(message, text):
    for page in text_pages(text):
        await message.answer(page)
