from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict

TZ = ZoneInfo("Asia/Taipei")
ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_ignore_empty=True)

    database_url: str
    data_dir: Path = ROOT / "data"
    llm_provider: str = "anthropic"  # anthropic / foundry / openai_compatible
    llm_foundry_resource: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model_fast: str = "claude-haiku-5-5"
    llm_model_strong: str = "claude-sonnet-5-5"
    llm_anthropic_fallbacks: bool = True
    llm_reasoning_effort: str | None = None
    llm_pricing_file: str = str(ROOT / "api" / "config" / "pricing.yaml")

    app_today: date | None = None  # 示範資料以 2026-11-10 為基準；未設定時用實際日期


settings = Settings()


def today() -> date:
    return settings.app_today or datetime.now(TZ).date()
