from functools import lru_cache

from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.exceptions import ConfigurationError
from core.modes import RuntimeEnvironment, TradingMode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", enable_decoding=False)

    app_env: RuntimeEnvironment = RuntimeEnvironment.DEVELOPMENT
    trading_mode: TradingMode = TradingMode.BACKTEST
    database_url: str = "sqlite:///./data/trading.db"
    log_level: str = "INFO"
    log_json: bool = True
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    allow_live_trading: bool = False
    mt5_login: int | None = None
    mt5_password: SecretStr | None = None
    mt5_server: str | None = None
    mt5_terminal_path: str | None = None
    mt5_timeout_ms: int = Field(default=60_000, ge=1_000, le=300_000)
    mt5_heartbeat_seconds: int = Field(default=15, ge=5, le=300)
    mt5_magic_number: int = Field(default=26_091_801, ge=1)
    mt5_max_spread_points: int = Field(default=30, ge=1)
    market_symbols: tuple[str, ...] = ("EURUSD", "GBPUSD", "USDJPY")
    market_timeframes: tuple[str, ...] = ("M1", "M5", "M15", "M30", "H1", "H4")
    emergency_position_policy: str = "LEAVE_UNCHANGED"
    control_recovery_operators: tuple[str, ...] = ()
    data_feed_stale_seconds: int = Field(default=120, ge=10)

    @field_validator("market_symbols", "market_timeframes", "control_recovery_operators", mode="before")
    @classmethod
    def csv_sequence(cls, value: object, info: ValidationInfo) -> object:
        if isinstance(value, str):
            values = tuple((part.strip().upper() if info.field_name != "control_recovery_operators" else part.strip()) for part in value.split(",") if part.strip())
            if not values and info.field_name == "control_recovery_operators":
                return ()
            if not values:
                raise ValueError("Market symbols/timeframes cannot be empty.")
            return values
        return value

    @field_validator("database_url")
    @classmethod
    def database_url_supported(cls, value: str) -> str:
        if not value.startswith(("sqlite:///", "postgresql+psycopg://", "postgresql://")):
            raise ValueError("DATABASE_URL must use sqlite or PostgreSQL.")
        return value

    @model_validator(mode="after")
    def protect_live_mode(self) -> "Settings":
        if self.trading_mode is TradingMode.LIVE:
            if self.app_env is not RuntimeEnvironment.PRODUCTION or not self.allow_live_trading:
                raise ValueError(
                    "LIVE requires APP_ENV=production and ALLOW_LIVE_TRADING=true; "
                    "LIVE is never enabled by default."
                )
        return self

    def assert_mode(self, requested: TradingMode) -> None:
        if requested is TradingMode.LIVE and self.trading_mode is not TradingMode.LIVE:
            raise ConfigurationError("Live execution is not enabled for this runtime.")


@lru_cache
def get_settings() -> Settings:
    return Settings()
