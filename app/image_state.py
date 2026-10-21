"""Persisted image states shared by generation, delivery, and recovery."""
from enum import StrEnum


class ImageState(StrEnum):
    GENERATING = 'generating'
    GENERATED = 'generated'
    DELIVERY_FAILED = 'delivery_failed'
    SENT = 'sent'
    FAILED = 'failed'
    UNKNOWN = 'unknown'
    INTERRUPTED = 'interrupted'


SENDABLE_STATES = frozenset((ImageState.GENERATED, ImageState.DELIVERY_FAILED))
RECOVERABLE_STATES = frozenset((ImageState.GENERATING, ImageState.INTERRUPTED, ImageState.UNKNOWN))
