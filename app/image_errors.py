"""Failures for which a paid generation may have reached the provider."""
import httpx
from openai import APIConnectionError, APIStatusError


class ImageOutcomeUnknown(RuntimeError):
    pass


UNKNOWN_OUTCOME_MESSAGE = ('主人，接口未返回可确认的结果，无法确认图片是否已生成或扣费。'
                           '我没有自动重试；重新生成可能再次扣费，请先核对账户记录。')


def unknown_outcome(exc):
    return (isinstance(exc, (ImageOutcomeUnknown, TimeoutError,
                            httpx.TransportError, APIConnectionError))
            or isinstance(exc, APIStatusError) and (exc.status_code >= 500 or exc.status_code == 408))
