from datetime import datetime, timedelta, timezone

from config.settings import Settings
from data.storage import RawMarketDataRepository
from data.types import MarketBar
from database.models import DatasetRow, FeatureSnapshot
from database.models import DatasetManifest
from database.session import initialize_database
from research.dataset_v1 import FEATURE_VERSION, ResearchDatasetV1Builder
from ai.brain_v1 import BrainV1Dataset
from collections import Counter, defaultdict
from sqlalchemy import func, select

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


def _build_three_symbols(tmp_path, order):
    sessions = initialize_database(Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / ('-'.join(order) + '.db')}"))
    raw = RawMarketDataRepository(sessions)
    start = datetime(2026, 1, 5, tzinfo=UTC)
    for symbol in order:
        raw.append_bars([bar(symbol, "M5", start + timedelta(minutes=5 * i), 1 + i / 10000) for i in range(240)])
        for timeframe, minutes in (("M15", 15), ("M30", 30), ("H1", 60), ("H4", 240)):
            raw.append_bars([bar(symbol, timeframe, start + timedelta(minutes=minutes * i), 1 + i / 10000) for i in range(20)])
    result = ResearchDatasetV1Builder(sessions).build_and_freeze(order)
    with sessions() as session:
        rows = session.scalars(select(DatasetRow).where(DatasetRow.dataset_id == result["dataset_id"])).all()
        manifest = session.scalar(select(DatasetManifest).where(DatasetManifest.dataset_id == result["dataset_id"]))
        counts = dict(session.execute(select(DatasetRow.split, func.count(DatasetRow.id)).where(
            DatasetRow.dataset_id == result["dataset_id"]).group_by(DatasetRow.split)).all())
    return sessions, result, rows, manifest, counts


def test_multisymbol_chronological_split_and_manifest_reconcile(tmp_path):
    symbols = ("EURUSD", "GBPUSD", "USDJPY")
    sessions, result, rows, manifest, counts = _build_three_symbols(tmp_path, symbols)
    _, reversed_result, reversed_rows, _, _ = _build_three_symbols(tmp_path, tuple(reversed(symbols)))
    assert result["dataset_version"] == "research-dataset-v2"
    assert result["content_hash"] == reversed_result["content_hash"]
    assignments = {(row.symbol, row.decision_at): row.split for row in rows}
    assert assignments == {(row.symbol, row.decision_at): row.split for row in reversed_rows}
    per_symbol = defaultdict(Counter)
    by_time = defaultdict(set)
    by_split = defaultdict(list)
    for row in rows:
        per_symbol[row.symbol][row.split] += 1
        by_time[row.decision_at].add(row.split)
        by_split[row.split].append(row.decision_at)
    assert all(per_symbol[symbol][split] > 0 for symbol in symbols for split in ("TRAIN", "VALIDATION", "OOS"))
    assert max(by_split["TRAIN"]) < min(by_split["VALIDATION"])
    assert max(by_split["VALIDATION"]) < min(by_split["OOS"])
    assert all(len(splits) == 1 for splits in by_time.values())
    assert counts == {split: result["counts"][f"{split.lower()}_rows"] for split in ("TRAIN", "VALIDATION", "OOS")}
    assert (manifest.train_rows, manifest.validation_rows, manifest.oos_rows) == (
        counts["TRAIN"], counts["VALIDATION"], counts["OOS"])
    assert sum(counts.values()) == manifest.usable_rows == len(rows)
    labels, _, metadata = BrainV1Dataset(sessions, result["content_hash"], result["dataset_version"]).rows()
    split_by_time = {(row.symbol, row.decision_at): row.split for row in rows}
    assert labels
    assert all(split_by_time[(item["symbol"], item["target_timestamp"])] == item["split"] for item in labels)
    assert sum(value for key, value in metadata["exclusions"].items() if key.startswith("LABEL_HORIZON_CROSSES_SPLIT")) > 0
