"""Bounded read-only MT5 forward observer; launch as a separate process."""
import argparse
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic, sleep

from ai.shadow import SYMBOLS, ShadowService
from config.settings import get_settings
from database.audit import AuditRepository
from database.models import SystemControlState
from database.session import initialize_database
from mt5.connection import MT5ConnectionManager
from mt5.runtime_gateway import MT5RuntimeGateway


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration-seconds", type=int, default=60)
    parser.add_argument("--poll-seconds", type=int, default=15)
    parser.add_argument("--until-each-symbol", action="store_true",
        help="Stop after one new forward decision exists for every supported symbol.")
    args = parser.parse_args()
    if args.duration_seconds < 0 or args.poll_seconds < 1:
        raise ValueError("Polling bounds must be positive.")
    settings = get_settings()
    if settings.allow_live_trading or settings.trading_mode.value != "SHADOW":
        raise RuntimeError("Shadow requires TRADING_MODE=SHADOW and LIVE permission false.")
    sessions = initialize_database(settings)
    manager = MT5ConnectionManager(settings, AuditRepository(sessions))
    started = datetime.now(timezone.utc)
    events = []
    new_decisions = Counter()
    market = {}
    stop_reason = None
    try:
        manager.connect()
        observer = ShadowService(sessions, MT5RuntimeGateway(manager), settings)
        with sessions() as session:
            state = session.get(SystemControlState, "brain_v2_shadow_service")
            state.value = {**state.value, "observer_pid": os.getpid(), "poll_seconds": args.poll_seconds,
                "status": "OBSERVING", "stop_reason": None}
            session.commit()
        until = monotonic() + args.duration_seconds
        while True:
            event = observer.poll_once()
            events.append(event)
            new_decisions.update(event["created"])
            if args.until_each_symbol and all(new_decisions[symbol] > 0 for symbol in SYMBOLS):
                stop_reason = "ALL_SYMBOLS_OBSERVED"
                break
            if monotonic() >= until:
                stop_reason = "TIME_LIMIT"
                break
            sleep(min(args.poll_seconds, max(0, until - monotonic())))
        observed_at = datetime.now(timezone.utc)
        for symbol in SYMBOLS:
            bars = observer._bars(symbol, "M5", observed_at)
            last = bars[-1] if bars else None
            market[symbol] = {"last_genuine_closed_m5_open": last.timestamp.isoformat() if last else None,
                "last_genuine_closed_m5_decision_time": (last.timestamp + timedelta(minutes=5)).isoformat() if last else None,
                "new_closed_candle_since_observer_start": bool(last and last.timestamp + timedelta(minutes=5) > observer.started_at)}
        with sessions() as session:
            state = session.get(SystemControlState, "brain_v2_shadow_service")
            state.value = {**state.value, "status": "STOPPED", "stop_reason": stop_reason}
            session.commit()
    except Exception as exc:
        with sessions() as session:
            state = session.get(SystemControlState, "brain_v2_shadow_service")
            if state:
                state.value = {**state.value, "status": "ERROR", "stop_reason": type(exc).__name__}
                session.commit()
        raise
    finally:
        if manager.connected:
            manager.disconnect()
    report = {"runtime_start": started.isoformat(), "runtime_end": datetime.now(timezone.utc).isoformat(),
        "polls": events, "market": market, "trading_mode": settings.trading_mode.value,
        "new_decisions": dict(new_decisions), "stop_reason": stop_reason,
        "orders_submitted_by_shadow": 0, "live_permission": settings.allow_live_trading}
    Path("reports/shadow_runtime.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
