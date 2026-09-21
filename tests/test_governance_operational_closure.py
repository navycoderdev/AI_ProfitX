from datetime import datetime, timedelta, timezone

from config.settings import Settings
from database.models import DatasetManifest, RawMarketBar
from database.session import initialize_database
from data.operations import quality_report
from data.storage import RawMarketDataRepository
from data.types import MarketBar
from research.governance import (DatasetSpec, QualityReasonCode, contaminated_decision_times,
    dependency_audit, reason_codes_from_quality)
from research.repository import DatasetGovernanceRepository

UTC = timezone.utc


def setup_repo(tmp_path):
    sessions = initialize_database(Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'governance.db'}"))
    return sessions, RawMarketDataRepository(sessions), DatasetGovernanceRepository(sessions)


def test_quarantine_persists_with_half_open_boundaries_and_no_raw_mutation(tmp_path):
    sessions, raw, governance = setup_repo(tmp_path); at = datetime(2026, 1, 1, tzinfo=UTC)
    raw.append_bars([MarketBar("TEST", "USDJPY", "M1", at, 1, 2, .5, 1.5)])
    before = raw.count_bars(); mask = governance.quarantine(policy_version="q-v1", symbol="USDJPY", timeframe="M1",
        start_time=at + timedelta(minutes=1), end_time=at + timedelta(minutes=2),
        reason_codes=[QualityReasonCode.POSSIBLE_DATA_GAP.value], source_quality_run_id="run-1")
    assert raw.count_bars() == before
    assert governance.active_at("USDJPY", "M1", mask.start_time)
    assert not governance.active_at("USDJPY", "M1", mask.end_time)
    assert governance.masks("USDJPY", "M1")[0].reason_codes == ["POSSIBLE_DATA_GAP"]


def test_superseded_quarantine_remains_auditable_but_is_not_active(tmp_path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'superseded.db'}")
    sessions = initialize_database(settings)
    governance = DatasetGovernanceRepository(sessions)
    start = datetime(2026, 9, 20, tzinfo=timezone.utc)
    governance.quarantine(policy_version="review-v1", symbol="BTCUSD", timeframe="M1",
        start_time=start, end_time=start + timedelta(minutes=1), reason_codes=["BAD_DATA"],
        source_quality_run_id=None)
    assert governance.supersede(policy_version="review-v1", reason_code="BAD_DATA") == 1
    assert governance.masks("BTCUSD", "M1") == []
    assert governance.active_at("BTCUSD", "M1", start) == []


def test_quality_adapter_preserves_multiple_reason_codes(tmp_path):
    _, raw, _ = setup_repo(tmp_path); at = datetime(2026, 1, 5, tzinfo=UTC)
    raw.append_bars([MarketBar("TEST", "EURUSD", "M1", at, 1, 2, .5, 1.5),
        MarketBar("TEST", "EURUSD", "M1", at + timedelta(minutes=3), 1, 2, .5, 1.5)])
    report = quality_report(raw, "EURUSD", "M1", at, at + timedelta(minutes=3), tmp_path / "reports")
    assert "POSSIBLE_DATA_GAP" in report["reason_codes"]
    assert reason_codes_from_quality({"gaps": [{"classification": "POSSIBLE_DATA_GAP"}], "outliers": [1],
        "data_errors": [{"reason": "duplicate_timestamp"}]}) == ["DUPLICATE_TIMESTAMP", "POSSIBLE_DATA_GAP", "RANGE_OUTLIER"]


def test_not_built_manifest_has_null_build_fields(tmp_path):
    sessions, _, governance = setup_repo(tmp_path); manifest = governance.register_not_built()
    assert manifest.state == "NOT_BUILT" and manifest.content_hash is None and manifest.candidate_rows is None
    with sessions() as session: assert session.get(DatasetManifest, manifest.id).usable_rows is None


def test_dependency_contamination_and_incomplete_metadata_blocking():
    at = datetime(2026, 1, 1, 10, tzinfo=UTC)
    times = [at + timedelta(minutes=5 * i) for i in range(5)]
    contaminated = contaminated_decision_times(at, at + timedelta(minutes=2), "M1", 2, 1, times)
    assert contaminated[:1] == [at]
    audit = dependency_audit([{"name": "valid", "version": "v1", "dependency": {"source_timeframe": "M5", "lookback": 2, "warmup": 1}},
        {"name": "unknown", "version": "v1"}])
    assert audit["freeze_blocked"] and audit["incomplete"][0]["reason_code"] == "DEPENDENCY_METADATA_INCOMPLETE"
