from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    telegram_bot_token: str
    openai_api_key: str
    allowed_user_ids: str
    text_model: str = 'gpt-4.1-mini'
    image_model: str = 'gpt-image-1-mini'
    database_path: str = '/app/data/gptchan.sqlite3'
    max_context_messages: int = 24
    max_context_chars: int = 32000
    memory_limit: int = Field(default=12, ge=1, le=100)
    run_startup_probe: bool = False
    image_quality: Literal['low', 'medium', 'high'] = 'medium'
    image_hourly_limit: int = Field(default=12, ge=1, le=100)
    image_cooldown_seconds: int = Field(default=15, ge=0, le=3600)

    @property
    def allowed_ids(self) -> set[int]:
        return {int(x.strip()) for x in self.allowed_user_ids.split(',') if x.strip()}

