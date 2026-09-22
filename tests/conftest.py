import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def no_ambient_model_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the suite off the live provider.

    A developer's `.env` holds a real key, and `Settings` reads it. Without this
    the suite would bill real calls and its results would depend on a model's
    output. Tests that exercise a model pass build their own `Settings` and
    inject a stub client, so they are unaffected.
    """
    get_settings.cache_clear()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.config.Settings.model_config",
        {**get_settings().model_config, "env_file": None},
        raising=False,
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
