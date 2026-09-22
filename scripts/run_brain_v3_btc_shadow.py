"""Bounded observation-only Brain-v3 BTCUSD forward verification."""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic, sleep

from ai.shadow_v3 import BrainV3BTCShadowService, STATUS_KEY
from config.settings import get_settings
from database.audit import AuditRepository
from database.models import ShadowDecision, ShadowOutcome, SystemControlState
from database.session import initialize_database
from mt5.connection import MT5ConnectionManager
from mt5.runtime_gateway import MT5RuntimeGateway
from sqlalchemy import func, select


def main() -> None:
    settings = get_settings()
    if settings.allow_live_trading or settings.trading_mode.value != "SHADOW":
        raise RuntimeError("BTCUSD Shadow requires SHADOW and LIVE disabled.")
    sessions = initialize_database(settings)
    terminal_settings = settings.model_copy(update={"mt5_login": None, "mt5_password": None, "mt5_server": None})
    manager = MT5ConnectionManager(terminal_settings, AuditRepository(sessions))
    started, events, new = datetime.now(timezone.utc), [], Counter()
    try:
        manager.connect()
        observer = BrainV3BTCShadowService(sessions, MT5RuntimeGateway(manager), settings)
        # A resumed observer may only accept candles that closed after this
        # process began.  The persisted service boundary is lineage metadata,
        # not permission to backfill candles missed while the process was down.
        observer.started_at = max(observer.started_at, started)
        until = monotonic() + 420
        while monotonic() < until and new["BTCUSD"] == 0:
            event = observer.poll_once(); events.append(event); new.update(event["created"])
            if new["BTCUSD"] == 0: sleep(min(15, max(0, until - monotonic())))
        reason = "BTCUSD_OBSERVED" if new["BTCUSD"] else "OBSERVER_TIMEOUT"
        with sessions() as session:
            state = session.get(SystemControlState, STATUS_KEY)
            state.value = {**state.value, "status": "STOPPED", "stop_reason": reason,
                           "stopped_at": datetime.now(timezone.utc).isoformat()}
            session.commit()
            counts = dict(session.execute(select(ShadowDecision.final_decision, func.count(ShadowDecision.id)).where(
                ShadowDecision.model_version == "Brain-v3", ShadowDecision.symbol == "BTCUSD").group_by(
                ShadowDecision.final_decision)).all())
            risk = dict(session.execute(select(ShadowDecision.risk_status, func.count(ShadowDecision.id)).where(
                ShadowDecision.model_version == "Brain-v3", ShadowDecision.symbol == "BTCUSD").group_by(
                ShadowDecision.risk_status)).all())
            pending = session.scalar(select(func.count(ShadowDecision.id)).outerjoin(ShadowOutcome,
                ShadowOutcome.decision_id == ShadowDecision.decision_id).where(ShadowDecision.model_version == "Brain-v3",
                ShadowDecision.symbol == "BTCUSD", ShadowOutcome.id.is_(None))) or 0
        report = {"runtime_start": started.isoformat(), "runtime_end": datetime.now(timezone.utc).isoformat(),
            "source": "MT5:ICMarketsSC-Demo", "model_version": "Brain-v3", "symbol": "BTCUSD",
            "events": events, "new_decisions": dict(new), "decision_counts": counts, "risk_counts": risk,
            "outcomes_pending": pending, "stop_reason": reason, "orders_submitted": 0,
            "runtime_paper_trades": 0, "live_permission": False}
        Path("reports/brain_v3_btc_shadow.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(report, indent=2, sort_keys=True))
    finally:
        if manager.connected: manager.disconnect()


if __name__ == "__main__":
    main()
