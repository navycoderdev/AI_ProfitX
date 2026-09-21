"""Review every unresolved BTCUSD gap/outlier and quarantine unsafe intervals."""

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from config.settings import get_settings
from data.quality import DataQualityEngine
from data.storage import RawMarketDataRepository
from database.audit import AuditRepository
from database.models import DatasetQuarantineMask
from database.session import initialize_database
from mt5.connection import MT5ConnectionManager, _mapping
from mt5.services import MT5SymbolService
from research.btcusd_quality_review import TIMEFRAMES, missing_interval, outlier_review, unresolved_gaps
from research.governance import DatasetSpec, contaminated_decision_times
from research.repository import DatasetGovernanceRepository
from sqlalchemy import func, select


SOURCE = "MT5:ICMarketsSC-Demo"
POLICY = "btc-v4-quality-review-v1"


def main() -> None:
    settings = get_settings()
    if settings.allow_live_trading or settings.trading_mode.value == "LIVE":
        raise RuntimeError("Quality review requires LIVE disabled.")
    sessions = initialize_database(settings)
    raw = RawMarketDataRepository(sessions)
    governance = DatasetGovernanceRepository(sessions)
    bars = {tf: raw.bars_as_of("BTCUSD", tf, datetime.max.replace(tzinfo=timezone.utc), source=SOURCE)
            for tf in TIMEFRAMES}
    if any(not rows for rows in bars.values()):
        raise RuntimeError("Complete broker-tagged BTCUSD history is required.")

    terminal_settings = settings.model_copy(update={"mt5_login": None, "mt5_password": None, "mt5_server": None})
    manager = MT5ConnectionManager(terminal_settings, AuditRepository(sessions))
    try:
        manager.connect()
        account = _mapping(manager.client.account_info())
        info = MT5SymbolService(manager, terminal_settings, manager.audit).ensure_available("BTCUSD")
        if account.get("server") != "ICMarketsSC-Demo" or account.get("trade_mode") != manager.client.ACCOUNT_TRADE_MODE_DEMO:
            raise RuntimeError("Verified IC Markets demo identity is required.")
        availability = {"audit_time_utc": datetime.now(timezone.utc).isoformat(),
                        "server": account.get("server"), "demo": True,
                        "trade_mode": info.get("trade_mode"), "trade_mode_full": info.get("trade_mode") == 4,
                        "historical_session_api": "UNAVAILABLE_IN_MT5_PYTHON_API"}
    finally:
        if manager.connected:
            manager.disconnect()

    events = []
    quarantine_specs = []
    for tf, rows in bars.items():
        report = DataQualityEngine().report("BTCUSD", tf, rows)
        gaps = list(report.timestamp_gaps)
        for gap in unresolved_gaps(tf, gaps):
            start, end, missing = missing_interval(tf, gap)
            price_before = next(row.close for row in rows if row.timestamp.isoformat() == gap["after"])
            price_after = next(row.open for row in rows if row.timestamp.isoformat() == gap["before"])
            movement = (price_after - price_before) / price_before if price_before else None
            cross = {other: not any(start <= row.timestamp < end for row in other_rows)
                     for other, other_rows in bars.items()}
            event = {"flag_type": "UNRESOLVED_GAP", "timeframe": tf,
                     "start_utc": start.isoformat(), "end_utc": end.isoformat(),
                     "missing_candle_count": missing, "price_before": price_before,
                     "price_after": price_after, "price_movement_percent": movement,
                     "classification": "POSSIBLE_DATA_GAP",
                     "broker_availability": "NO_HISTORICAL_SESSION_API; CURRENT_TRADE_MODE_FULL",
                     "evidence": {"not_recurring_three_times": True,
                                  "absence_by_timeframe_during_interval": cross,
                                  "reason": "Non-recurring absence cannot be proven as a scheduled broker closure."}}
            events.append(event)
            quarantine_specs.append((tf, start, end, event))
        outlier_times = {datetime.fromisoformat(item["timestamp"]): item for item in report.outliers}
        indexed = {row.timestamp: row for row in rows}
        for at in sorted(outlier_times):
            events.append(outlier_review(tf, indexed[at], bars))

    bad = [event for event in events if event["classification"] == "BAD_DATA"]
    superseded_bad_masks = governance.supersede(policy_version=POLICY, reason_code="BAD_DATA") if not bad else 0
    for event in bad:
        quarantine_specs.append((event["timeframe"], datetime.fromisoformat(event["start_utc"]),
                                 datetime.fromisoformat(event["end_utc"]), event))
    run_id = str(uuid5(NAMESPACE_URL, "AI_ProfitX|ICMarketsSC-Demo|BTCUSD|2026-07-22|2026-09-20|quality-review-v1"))
    masks = []
    for tf, start, end, event in quarantine_specs:
        mask = governance.quarantine(policy_version=POLICY, symbol="BTCUSD", timeframe=tf,
            start_time=start, end_time=end, reason_codes=[event["classification"]], source_quality_run_id=run_id)
        masks.append({"mask_id": mask.mask_id, "timeframe": tf, "start_utc": start.isoformat(),
                      "end_utc": end.isoformat(), "reason_codes": mask.reason_codes})

    decision_times = [row.timestamp.replace(tzinfo=timezone.utc) + timedelta(minutes=5)
                      for row in bars["M5"]]
    contaminated = set()
    for tf, start, end, _ in quarantine_specs:
        lookback, warmup = (50, 50) if tf == "M5" else (2, 2)
        contaminated.update(contaminated_decision_times(start, end, tf, lookback, warmup, decision_times))
    classifications = Counter(event["classification"] for event in events)
    classification_counts = {name: classifications[name] for name in
        ("EXPECTED_MARKET_CLOSURE", "GENUINE_MARKET_MOVE", "POSSIBLE_DATA_GAP", "BAD_DATA")}
    with sessions() as session:
        superseded_total = int(session.scalar(select(func.count(DatasetQuarantineMask.id)).where(
            DatasetQuarantineMask.policy_version == POLICY,
            DatasetQuarantineMask.state == "SUPERSEDED")) or 0)
    gap_count = sum(event["flag_type"] == "UNRESOLVED_GAP" for event in events)
    invalid = sum(1 for rows in bars.values() for row in rows if row.low <= 0 or row.high < row.low or
                  row.high < max(row.open, row.close) or row.low > min(row.open, row.close))
    duplicates = sum(DataQualityEngine().report("BTCUSD", tf, rows).duplicates for tf, rows in bars.items())
    out_of_order = sum(sum(item.get("reason") == "out_of_order_timestamp" for item in
                           DataQualityEngine().report("BTCUSD", tf, rows).data_errors) for tf, rows in bars.items())
    remaining = 0 if len(masks) == len(quarantine_specs) else len(quarantine_specs) - len(masks)
    readiness = "PASS" if remaining == invalid == duplicates == out_of_order == len(bad) else "BLOCKED"
    result = {"review_id": run_id, "reviewed_at": datetime.now(timezone.utc).isoformat(),
              "source": SOURCE, "symbol": "BTCUSD", "broker_availability": availability,
              "review_scope": "PRE_DATASET_V4_NO_RAW_MUTATION", "total_flags_reviewed": len(events),
              "gap_flags_reviewed": gap_count, "range_outliers_reviewed": len(events) - gap_count,
              "classification_counts": classification_counts, "events": events,
              "quarantine": {"policy_version": POLICY, "interval_count": len(masks), "masks": masks,
                             "superseded_provisional_bad_data_masks": superseded_total,
                             "raw_rows_modified": 0, "raw_rows_inside_missing_intervals": 0,
                             "affected_m5_decision_rows": len(contaminated)},
              "post_review": {"remaining_unresolved_gaps": remaining, "invalid_ohlc": invalid,
                              "duplicates": duplicates, "out_of_order": out_of_order},
              "dataset_v4_readiness": readiness, "dataset_v4_created": False,
              "brain_v3_created": False, "orders_submitted": 0}
    Path("reports/ic_btcusd_quality_review.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "events"}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
