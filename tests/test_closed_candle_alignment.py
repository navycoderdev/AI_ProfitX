from datetime import datetime, timezone

import pytest

from config.settings import Settings
from data.storage import RawMarketDataRepository
from data.synchronization import MultiTimeframeSynchronizer
from data.types import MarketBar
from database.session import initialize_database


@pytest.mark.parametrize("timeframe,decision_hour", [("M15", 10), ("M30", 10), ("H1", 10), ("H4", 12)])
def test_forming_higher_timeframe_candle_is_not_available(tmp_path, timeframe, decision_hour):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / (timeframe + '.db')}")
    repo = RawMarketDataRepository(initialize_database(settings)); utc = timezone.utc
    open_time = datetime(2026, 1, 1, decision_hour, tzinfo=utc)
    repo.append_bars([MarketBar("TEST", "EURUSD", timeframe, open_time, 1, 2, .5, 1.5)])
    assert MultiTimeframeSynchronizer(repo).latest_closed("EURUSD", timeframe, open_time) is None


def test_exactly_closed_context_is_available(tmp_path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'exact.db'}"); repo = RawMarketDataRepository(initialize_database(settings)); utc = timezone.utc
    open_time = datetime(2026, 1, 1, 10, tzinfo=utc)
    repo.append_bars([MarketBar("TEST", "EURUSD", "M15", open_time, 1, 2, .5, 1.5)])
    assert MultiTimeframeSynchronizer(repo).latest_closed("EURUSD", "M15", datetime(2026, 1, 1, 10, 15, tzinfo=utc)).timestamp == open_time
