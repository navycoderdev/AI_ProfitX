import pytest
from pydantic import ValidationError
from config.settings import Settings

def test_defaults_are_safe():
    settings = Settings(_env_file=None)
    assert settings.trading_mode.value == "BACKTEST"
    assert settings.allow_live_trading is False

def test_live_requires_three_explicit_controls():
    with pytest.raises(ValidationError):
        Settings(app_env="development", trading_mode="LIVE", allow_live_trading=False)

def test_live_permitted_only_in_explicit_production():
    settings = Settings(app_env="production", trading_mode="LIVE", allow_live_trading=True)
    assert settings.trading_mode.value == "LIVE"
