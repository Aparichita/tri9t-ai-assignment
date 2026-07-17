from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'app.db'}"
    generations_dir: str = str(BASE_DIR / "data" / "generations")
    gemini_max_retries: int = 2


settings = Settings()
