from datetime import datetime, timedelta, timezone

from research import label_audit


def test_twelve_bar_label_never_crosses_chronological_split(monkeypatch):
    monkeypatch.setattr(label_audit, "_numeric", lambda values: values)
    start = datetime(2026, 8, 3, tzinfo=timezone.utc)
    rows, close = [], {}
    for index in range(30):
        at = start + timedelta(minutes=5 * index)
        split = "TRAIN" if index < 15 else "VALIDATION"
        rows.append({"symbol": "XAUUSD", "decision_time": (at + timedelta(minutes=5)).isoformat(),
            "raw_data_cutoff": at.isoformat(), "split": split, "values": {}})
        close[("XAUUSD", at)] = 4000 + index
    result = label_audit.audit_fixed_labels(rows, close)
    assert result["exclusions"]["LABEL_HORIZON_CROSSES_SPLIT|XAUUSD|TRAIN"] == 12
    assert result["exclusions"]["LABEL_HORIZON_UNAVAILABLE|XAUUSD|VALIDATION"] == 12
    assert result["included_cross_split_labels"] == 0
    assert sum(result["class_distribution"]["XAUUSD"]["TRAIN"].values()) == 3
    assert sum(result["class_distribution"]["XAUUSD"]["VALIDATION"].values()) == 3
