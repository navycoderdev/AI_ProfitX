class TradingPlatformError(Exception):
    """Base exception for expected platform failures."""


class ConfigurationError(TradingPlatformError):
    """Raised when settings are invalid or unsafe."""


class GatewayUnavailableError(TradingPlatformError):
    """Raised when an MT5 operation is requested without a live gateway."""


class RiskRejectedError(TradingPlatformError):
    """Raised only by a risk implementation when it rejects a proposal."""


class ModeViolationError(TradingPlatformError):
    """Raised when a component violates its operating-mode boundary."""
