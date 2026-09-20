from datetime import datetime, timedelta, timezone

from config.settings import Settings
from data.storage import RawMarketDataRepository
from data.types import MarketBar
from database.models import DatasetRow, FeatureSnapshot
from database.session import initialize_database
from research.dataset_v1 import FEATURE_VERSION, ResearchDatasetV1Builder

UTC = timezone.utc


def bar(symbol, timeframe, at, price):
    return MarketBar("TEST", symbol, timeframe, at, price, price + .002, price - .002, price + .001, 1, 1, .0001)


def test_dataset_v1_builds_real_past_only_snapshots_and_freezes(tmp_path):
    sessions = initialize_database(Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'dataset.db'}")); raw = RawMarketDataRepository(sessions)
    start = datetime(2026, 1, 5, tzinfo=UTC)
    for symbol in ("EURUSD",):
        raw.append_bars([bar(symbol, "M5", start + timedelta(minutes=5 * i), 1 + i / 10000) for i in range(140)])
        for timeframe, minutes in (("M15", 15), ("M30", 30), ("H1", 60), ("H4", 240)):
            raw.append_bars([bar(symbol, timeframe, start + timedelta(minutes=minutes * i), 1 + i / 10000) for i in range(20)])
    result = ResearchDatasetV1Builder(sessions).build_and_freeze(("EURUSD",))
    assert result["state"] == "FROZEN" and result["counts"]["usable_rows"] > 0 and result["leakage_status"] == "PASS"
    with sessions() as session:
        snapshots = list(session.query(FeatureSnapshot)); rows = list(session.query(DatasetRow))
    assert len(snapshots) == len(rows) == result["counts"]["usable_rows"]
    assert all(snapshot.raw_data_cutoff <= snapshot.decision_at for snapshot in snapshots)
    assert all(snapshot.feature_version == FEATURE_VERSION for snapshot in snapshots)
