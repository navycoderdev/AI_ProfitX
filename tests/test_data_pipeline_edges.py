from datetime import datetime, timedelta, timezone

import pytest

from data.normalizer import DataNormalizer
from data.operations import resolve_broker_symbol
from data.quality import DataQualityEngine
from data.storage import RawMarketDataRepository
from data.types import MarketBar, MarketTick
from database.session import initialize_database
from config.settings import Settings


UTC = timezone.utc


def bar(at: datetime, high: float = 1.2, low: float = 1.0) -> MarketBar:
    return MarketBar("TEST", "EURUSD", "M1", at, 1.1, high, low, 1.1, 1, 1, 0)


class Catalogue:
    def __init__(self, names): self.names = names; self.mt5 = self
    def ensure_available(self, name):
        if name in self.names: return {}
        raise ValueError(name)
    def symbols_get(self):
        return [type("Symbol", (), {"name": name})() for name in self.names]


@pytest.mark.parametrize("broker", ["EURUSDm", "EURUSD.a", "mEURUSD"])
def test_broker_suffix_and_prefix_resolution_preserves_broker_name(broker):
    assert resolve_broker_symbol(Catalogue([broker]), "EURUSD") == broker


def test_broker_resolution_rejects_unsupported_and_ambiguous_catalogues():
    with pytest.raises(ValueError, match="No broker symbol"):
        resolve_broker_symbol(Catalogue(["GBPUSDm"]), "EURUSD")
    with pytest.raises(ValueError, match="Ambiguous"):
        resolve_broker_symbol(Catalogue(["EURUSDm", "EURUSD.a"]), "EURUSD")


def test_weekend_and_intraday_gap_detection_are_distinct():
    friday = datetime(2026, 9, 11, 23, 59, tzinfo=UTC)
    monday = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)
    short = monday + timedelta(minutes=3)
    report = DataQualityEngine().report("EURUSD", "M1", [bar(friday), bar(monday), bar(short)])
    assert len(report.timestamp_gaps) == 2
    assert report.timestamp_gaps[0]["duration_seconds"] > 60 * 60
    assert report.timestamp_gaps[1]["duration_seconds"] == 180


def test_raw_repository_idempotency_and_utc_boundary(tmp_path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'edge.db'}")
    repository = RawMarketDataRepository(initialize_database(settings))
    start = datetime(2026, 1, 1, tzinfo=UTC)
    observations = [bar(start + timedelta(minutes=index)) for index in range(3)]
    assert repository.append_bars(observations) == 3
    assert repository.append_bars(observations) == 0
    restored = repository.bars_as_of("EURUSD", "M1", start + timedelta(minutes=2))
    assert len(restored) == 3 and all(item.timestamp.tzinfo is not None for item in restored)


def test_invalid_ohlc_is_detectable():
    report = DataQualityEngine().report("EURUSD", "M1", [bar(datetime(2026, 1, 1, tzinfo=UTC), high=1.0, low=1.2)])
    assert report.observations == 1
