"""Bounded read-only MT5 forward observer; launch as a separate process."""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic, sleep

from ai.shadow import SYMBOLS, ShadowService
from config.settings import get_settings
from database.audit import AuditRepository
from database.session import initialize_database
from mt5.connection import MT5ConnectionManager
from mt5.runtime_gateway import MT5RuntimeGateway


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration-seconds", type=int, default=60)
    parser.add_argument("--poll-seconds", type=int, default=15)
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
    market = {}
    try:
        manager.connect()
        observer = ShadowService(sessions, MT5RuntimeGateway(manager), settings)
        until = monotonic() + args.duration_seconds
        while True:
            events.append(observer.poll_once())
            if monotonic() >= until:
                break
            sleep(min(args.poll_seconds, max(0, until - monotonic())))
        observed_at = datetime.now(timezone.utc)
        for symbol in SYMBOLS:
            bars = observer._bars(symbol, "M5", observed_at)
            last = bars[-1] if bars else None
            market[symbol] = {"last_genuine_closed_m5_open": last.timestamp.isoformat() if last else None,
                "last_genuine_closed_m5_decision_time": (last.timestamp + timedelta(minutes=5)).isoformat() if last else None,
                "new_closed_candle_since_observer_start": bool(last and last.timestamp + timedelta(minutes=5) > observer.started_at)}
    finally:
        if manager.connected:
            manager.disconnect()
    report = {"runtime_start": started.isoformat(), "runtime_end": datetime.now(timezone.utc).isoformat(),
        "polls": events, "market": market, "trading_mode": settings.trading_mode.value,
        "orders_submitted_by_shadow": 0, "live_permission": settings.allow_live_trading}
    Path("reports/shadow_runtime.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
