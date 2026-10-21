"""One backend decision shared by the Telegram handler and provider client."""
from enum import StrEnum

from app.tool_errors import ToolProtocolError


class ImageBackend(StrEnum):
    OPENAI = 'openai'
    NOVELAI = 'novelai'


def image_backend(nsfw: bool) -> ImageBackend:
    if type(nsfw) is not bool:
        raise ToolProtocolError('invalid_arguments')
    return ImageBackend.NOVELAI if nsfw else ImageBackend.OPENAI
