from research.governance import DatasetSpec, QualityReasonCode, can_freeze, content_hash, quality_status


def test_reason_severity_and_multiple_codes_are_deterministic():
    assert quality_status({QualityReasonCode.EXPECTED_MARKET_CLOSURE}) == "PASS"
    assert quality_status({QualityReasonCode.POSSIBLE_DATA_GAP, QualityReasonCode.RANGE_OUTLIER}) == "WARNING"
    assert quality_status({QualityReasonCode.POSSIBLE_DATA_GAP, QualityReasonCode.INVALID_OHLC}) == "FAIL"


def test_dataset_spec_and_hash_are_reproducible_and_sensitive():
    spec = DatasetSpec(); rows = [{"symbol": "EURUSD", "decision_time": "2026-01-01T00:05:00Z", "x": 1.0}]
    assert content_hash(rows, spec) == content_hash(list(rows), spec)
    assert content_hash(rows, spec) != content_hash([{**rows[0], "x": 2.0}], spec)


def test_freeze_gate_blocks_missing_or_fatal_quality():
    assert not can_freeze(set(), False)
    from research.governance import FREEZE_GATES
    assert not can_freeze(FREEZE_GATES, True)
    assert can_freeze(FREEZE_GATES, False)
