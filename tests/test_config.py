import pytest

from sprout import config


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: False)
    for var in ("ANTHROPIC_API_KEY", "COMPANIES_HOUSE_API_KEY", "SPROUT_BULK_MODEL"):
        monkeypatch.delenv(var, raising=False)


def test_missing_keys_raise_clear_error():
    s = config.get_settings()
    with pytest.raises(config.MissingConfigError, match="ANTHROPIC_API_KEY"):
        s.require_anthropic_key()
    with pytest.raises(config.MissingConfigError, match="COMPANIES_HOUSE_API_KEY"):
        s.require_companies_house_key()


def test_empty_key_counts_as_missing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert config.get_settings().anthropic_api_key is None


def test_defaults_and_overrides(monkeypatch):
    assert config.get_settings().bulk_model == config.DEFAULT_BULK_MODEL
    monkeypatch.setenv("SPROUT_BULK_MODEL", "other-model")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    s = config.get_settings()
    assert s.bulk_model == "other-model"
    assert s.require_anthropic_key() == "k"
