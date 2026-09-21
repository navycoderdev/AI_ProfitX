"""Preserve the stopped Brain-v2 observer state before changing MT5 brokers."""

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from ai.shadow import ARTIFACT_SHA256, DATASET_HASH
from config.settings import get_settings
from database.models import ShadowDecision, SystemControlState
from database.session import initialize_database


def main() -> None:
    path = Path("reports/shadow_broker_switch.json")
    if path.exists():
        raise RuntimeError("Immutable broker-switch evidence already exists.")
    settings = get_settings()
    if settings.allow_live_trading or settings.trading_mode.value == "LIVE":
        raise RuntimeError("Broker switch record requires LIVE disabled.")
    sessions = initialize_database(settings)
    now = datetime.now(timezone.utc)
    with sessions() as session:
        state = session.get(SystemControlState, "brain_v2_shadow_service")
        if state is None or state.value.get("observer_pid") != 25112:
            raise RuntimeError("Expected historical observer state is missing.")
        counts = dict(session.execute(select(ShadowDecision.symbol, func.count(ShadowDecision.id)).where(
            ShadowDecision.model_version == "Brain-v2").group_by(ShadowDecision.symbol)).all())
        prior = dict(state.value)
        state.value = {**prior, "status": "STOPPED", "stop_reason": "BROKER_ACCOUNT_SWITCH",
                       "stopped_at": now.isoformat(), "broker_server": "MetaQuotes-Demo"}
        session.commit()
    result = {"runtime_start": prior.get("started_at"), "runtime_end": now.isoformat(),
              "observer_pid": 25112, "observer_state": "STOPPED",
              "stop_reason": "BROKER_ACCOUNT_SWITCH", "broker_server": "MetaQuotes-Demo",
              "model_version": "Brain-v2", "artifact_hash": ARTIFACT_SHA256,
              "dataset_version": "research-dataset-v3", "dataset_hash": DATASET_HASH,
              "last_poll_at": prior.get("last_poll_at"), "decision_counts": counts,
              "orders_submitted": 0, "runtime_paper_trades": 0, "live_permission": False,
              "credentials_in_report": False}
    path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
