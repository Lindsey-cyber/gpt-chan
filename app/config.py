from typing import Literal
from pydantic import Field, field_validator
from zoneinfo import ZoneInfo
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    telegram_bot_token: str
    openai_api_key: str
    allowed_user_ids: str
    text_model: str = 'gpt-4.1-mini'
    image_model: str = 'gpt-image-2.5-sunburst'
    novelai_token: str = ''
    database_path: str = '/app/data/gptchan.sqlite3'
    max_context_messages: int = Field(default=24, ge=2, le=200)
    max_context_chars: int = Field(default=32000, ge=1000, le=200000)
    memory_limit: int = Field(default=12, ge=1, le=100)
    run_startup_probe: bool = False
    run_persona_probe: bool = False
    image_quality: Literal['low', 'medium', 'high'] = 'high'
    image_hourly_limit: int = Field(default=12, ge=1, le=100)
    image_cooldown_seconds: int = Field(default=15, ge=0, le=3600)

    proactive_enabled: bool = True
    proactive_timezone: str = 'Asia/Tokyo'
    proactive_daily_limit: int = Field(default=4, ge=2, le=5)

    @field_validator('proactive_timezone')
    @classmethod
    def valid_timezone(cls, value):
        ZoneInfo(value)
        return value

    @property
    def allowed_ids(self) -> set[int]:
        return {int(x.strip()) for x in self.allowed_user_ids.split(',') if x.strip()}

