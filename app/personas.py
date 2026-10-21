"""Immutable profiles; selection belongs to each user, never the shared Brain."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILES = {'bailongji': ('白龙姬','gptchan.md'), 'xiaobai': ('小白','xiaobai.md')}
ALIASES = {'白龙姬':'bailongji', '默认':'bailongji', '小白':'xiaobai', 'grok':'xiaobai'}


def resolve_profile(value):
    key = ALIASES.get(value.strip().lower(),value.strip().lower())
    return key if key in PROFILES else None


def profile_prompt(key):
    return (ROOT / 'personas' / PROFILES[key][1]).read_text(encoding='utf-8')
