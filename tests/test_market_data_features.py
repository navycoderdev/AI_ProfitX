from datetime import datetime, timedelta, timezone

import pytest

from config.settings import Settings
from data.candle_builder import CandleBuilder
from data.collectors import HistoricalDataCollector, LiveTickCollector
from data.normalizer import DataNormalizer
from data.quality import DataQualityEngine
from data.storage import RawMarketDataRepository
from data.synchronization import MultiTimeframeSynchronizer
from data.types import MarketBar, MarketTick
from database.session import initialize_database
from features.engine import FeatureEngine
from features.registry import FeatureRegistry
from features.snapshots import FeatureSnapshotService


@pytest.fixture
def store(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/market.db")
    return RawMarketDataRepository(initialize_database(settings)), settings


def bars(count: int = 60) -> list[MarketBar]:
    start = datetime(2026, 1, 2, tzinfo=timezone.utc)
    return [MarketBar("MT5", "EURUSD", "M5", start + timedelta(minutes=5 * index),
                      1.10 + index * .0001, 1.1003 + index * .0001, 1.0998 + index * .0001,
                      1.1001 + index * .0001, 10.0, 100.0 + index, 0.0001) for index in range(count)]


def test_raw_bars_are_append_only_and_as_of_isolated(store):
    repository, _settings = store
    source = bars(3)
    assert repository.append_bars(source) == 3
    assert repository.append_bars(source) == 0
    available = repository.bars_as_of("EURUSD", "M5", source[1].timestamp)
    assert len(available) == 2
    assert available[-1].close == source[1].close
    synchronized = MultiTimeframeSynchronizer(repository).as_of("EURUSD", ("M5",), source[1].timestamp)
    # At the second bar's open timestamp it is still forming; only the first
    # fully closed M5 observation may be used in close-based research data.
    assert len(synchronized["M5"]) == 1


def test_features_are_deterministic_and_do_not_use_future_data():
    source = bars()
    engine = FeatureEngine()
    cutoff = source[40].timestamp
    first = engine.calculate(source, cutoff)
    changed_future = source.copy()
    changed_future[-1] = MarketBar("MT5", "EURUSD", "M5", source[-1].timestamp, 1, 9, .1, 8)
    second = engine.calculate(changed_future, cutoff)
    assert first == second
    assert first["rsi_14"] is not None
    assert first["higher_timeframe_trend"] == "UNKNOWN"


def test_feature_registry_and_snapshot_are_reproducible(store):
    _repository, settings = store
    sessions = initialize_database(settings)
    registry = FeatureRegistry(sessions)
    registry.register("technical_and_structure", "phase3-v1", {"lookahead": False, "atr_period": 14})
    registry.register("technical_and_structure", "phase3-v1", {"lookahead": False, "atr_period": 14})
    with pytest.raises(ValueError):
        registry.register("technical_and_structure", "phase3-v1", {"lookahead": True})
    now = datetime.now(timezone.utc)
    snapshots = FeatureSnapshotService(sessions)
    snapshot_id = snapshots.save("EURUSD", "M5", now, "phase3-v1", now, {"rsi_14": 55.0}, {"session": "LONDON"})
    assert snapshots.get(snapshot_id)["values"]["rsi_14"] == 55.0
    with pytest.raises(ValueError):
        snapshots.save("EURUSD", "M5", now, "phase3-v1", now + timedelta(seconds=1), {})


def test_data_quality_detects_gaps_duplicates_and_outliers():
    source = bars(4)
    irregular = source[:2] + [source[1], MarketBar("MT5", "EURUSD", "M5", source[1].timestamp + timedelta(hours=1),
                                                     1, 2, 0, 1.5)]
    report = DataQualityEngine().report("EURUSD", "M5", irregular)
    assert report.duplicates == 1
    assert len(report.timestamp_gaps) == 1
    assert len(report.outliers) >= 1


def test_candle_builder_and_collectors(store):
    repository, _settings = store
    base = datetime(2026, 1, 2, 10, 0, 10, tzinfo=timezone.utc)
    builder = CandleBuilder("M1")
    done, current = builder.update(MarketTick("MT5", "EURUSD", base, 1.1, 1.1001))
    assert done is None and current.tick_volume == 1
    done, current = builder.update(MarketTick("MT5", "EURUSD", base + timedelta(minutes=1), 1.2, 1.2001))
    assert done is not None and current.timestamp > done.timestamp

    class FakeData:
        def rates(self, *_args): return [{"time": base.timestamp(), "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15, "tick_volume": 3, "spread": 2}]
        def tick(self, _symbol): return {"time": base.timestamp(), "bid": 1.1, "ask": 1.1001, "volume": 2}

    normalizer, fake = DataNormalizer(), FakeData()
    collector = HistoricalDataCollector(fake, repository, normalizer)
    assert collector.collect("EURUSD", "M5", 5, base, base + timedelta(minutes=5)) == 1
    tick = LiveTickCollector(fake, repository, normalizer).collect_once("EURUSD")
    assert tick.bid == 1.1
