"""Application settings loaded from environment / ``backend/.env``.

Plan: docs/dev-plan/01-kien-truc-va-cong-nghe.md §6, 07 §9, 10 §7.
Relative paths are resolved against the ``backend/`` directory, not the CWD,
so ``uv run`` from anywhere inside the repo behaves the same.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent


def _resolve(p: Path) -> Path:
    return p if p.is_absolute() else (BACKEND_DIR / p).resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["dev", "test", "demo", "prod"] = "dev"
    duckdb_path: Path = Path("data/megaday.duckdb")
    # 01 §6 names DATA_DIR/LIVE_DIR; 06/10 use MOCK_DATA_ROOT/LIVE_ROOT. Both accepted.
    data_dir: Path = Field(
        default=Path("../mock-data/out/demo"),
        validation_alias=AliasChoices("DATA_DIR", "MOCK_DATA_ROOT"),
    )
    live_dir: Path = Field(
        default=Path("../mock-data/out/demo/live_stream"),
        validation_alias=AliasChoices("LIVE_DIR", "LIVE_ROOT"),
    )
    live_enabled: bool = True
    live_poll_seconds: float = 2.0
    display_tz: str = "Asia/Ho_Chi_Minh"
    platform_fee_rate: float = Field(default=0.12, ge=0.0, le=0.3)
    shop_id: str = "shop-demo"

    openai_api_key: str | None = None
    openai_model_reasoning: str | None = None
    openai_model_fast: str | None = None
    openai_model_embed: str | None = None
    ai_enabled: bool = True
    ai_mode: Literal["live", "record", "replay"] = "live"
    ai_daily_token_budget: int = 2_000_000
    ai_session_token_budget: int = 50_000
    ai_demo_cache: bool = True

    demo_mode: bool = True
    mc_samples: int = Field(default=1000, ge=200, le=5000)
    recompute_top_n: int = 50
    orc_workers: int = 4
    default_seed: int = 20261111
    log_level: str = "INFO"
    log_json: bool = True
    # Vite dev server (09). Comma-separated in .env.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @field_validator(
        "openai_api_key",
        "openai_model_reasoning",
        "openai_model_fast",
        "openai_model_embed",
        mode="before",
    )
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("duckdb_path", "data_dir", "live_dir", mode="after")
    @classmethod
    def _abs_path(cls, v: Path) -> Path:
        return _resolve(v)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def ai_available(self) -> bool:
        """AI usable: enabled and either a key is set or we replay recordings."""
        if not self.ai_enabled:
            return False
        return self.ai_mode == "replay" or self.openai_api_key is not None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
