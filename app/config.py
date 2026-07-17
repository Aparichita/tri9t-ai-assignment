from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    groq_api_key: str = ""
    groq_model: str = "llama-3.1-8b-instant"
    groq_max_retries: int = 2
    groq_timeout_seconds: float = 60.0
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'app.db'}"
    generations_dir: str = str(BASE_DIR / "data" / "generations")

    # Backward-compatible aliases used by document_service.py (unchanged)
    @property
    def gemini_api_key(self) -> str:
        return self.groq_api_key

    @property
    def gemini_model(self) -> str:
        return self.groq_model

    @property
    def gemini_max_retries(self) -> int:
        return self.groq_max_retries


settings = Settings()
