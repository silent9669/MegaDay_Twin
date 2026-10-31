from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import BACKEND_DIR, Settings


def test_env_example_parses() -> None:
    s = Settings(_env_file=BACKEND_DIR / ".env.example")  # type: ignore[call-arg]
    assert s.openai_api_key is None  # example never ships a key
    assert s.openai_model_fast is None
    assert s.platform_fee_rate == pytest.approx(0.12)
    assert s.duckdb_path == (BACKEND_DIR / "data" / "megaday.duckdb").resolve()
    assert s.data_dir.name == "demo"
    assert s.cors_origin_list[0] == "http://localhost:5173"


def test_legacy_env_aliases(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LIVE_ROOT", str(tmp_path / "live"))
    monkeypatch.setenv("MOCK_DATA_ROOT", str(tmp_path / "hist"))
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.live_dir == tmp_path / "live"
    assert s.data_dir == tmp_path / "hist"


def test_ai_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    base = {"_env_file": None}
    assert not Settings(**base, ai_enabled=False, openai_api_key="sk-x").ai_available  # type: ignore[arg-type]
    assert not Settings(**base, ai_enabled=True, openai_api_key="").ai_available  # type: ignore[arg-type]
    assert Settings(**base, ai_enabled=True, openai_api_key="sk-x").ai_available  # type: ignore[arg-type]
    assert Settings(**base, ai_enabled=True, ai_mode="replay").ai_available  # type: ignore[arg-type]


def test_env_example_has_no_secret() -> None:
    text = (BACKEND_DIR / ".env.example").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("OPENAI_API_KEY="):
            assert line.strip() == "OPENAI_API_KEY="
    assert "sk-" not in text
