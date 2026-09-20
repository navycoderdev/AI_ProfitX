from datetime import datetime, timedelta, timezone

import pytest

from ai.types import ProposalAction, TradeProposal
from config.settings import Settings
from database.audit import AuditRepository
from database.session import initialize_database
from memory.repositories import TradeMemoryRepository
from models.registry import ModelRegistry
from monitoring.alerts import AlertService
from monitoring.control import EmergencyStopService
from monitoring.recovery import StartupRecoveryService
from monitoring.safety import ProposalSafetyValidator
from risk.guards import EmergencyKillSwitch


def services(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/operations.db", control_recovery_operators=("operator-1",))
    sessions = initialize_database(settings); audit, alerts = AuditRepository(sessions), AlertService(sessions)
    kill = EmergencyKillSwitch(); emergency = EmergencyStopService(sessions, settings, audit, kill)
    return settings, sessions, audit, alerts, emergency, kill


def test_emergency_stop_blocks_orders_persists_across_restart_and_requires_controlled_reactivation(tmp_path):
    _settings, _sessions, _audit, _alerts, emergency, kill = services(tmp_path)
    emergency.activate("operator-1", "risk event")
    with pytest.raises(Exception): emergency.assert_orders_allowed()
    assert kill.active is True
    recovered_state = emergency.recover_on_startup()
    assert recovered_state["active"] is True
    with pytest.raises(PermissionError): emergency.reactivate("unknown", "controlled_operator_reactivation")
    emergency.reactivate("operator-1", "controlled_operator_reactivation")
    emergency.assert_orders_allowed()


def test_alerts_cover_mt5_disconnect_stale_data_database_and_spread(tmp_path):
    _settings, _sessions, _audit, alerts, _emergency, _kill = services(tmp_path)
    alerts.evaluate(mt5_connected=False, database_ok=False, last_tick_at=datetime.now(timezone.utc) - timedelta(hours=1), stale_seconds=10,
                    spread_points=50, maximum_spread=20, errors=("EXECUTION_FAILURE", "MODEL_INFERENCE_FAILURE"))
    codes = {item["code"] for item in alerts.recent()}
    assert {"MT5_DISCONNECTED", "DATABASE_FAILURE", "DATA_FEED_STALE", "ABNORMAL_SPREAD", "EXECUTION_FAILURE", "MODEL_INFERENCE_FAILURE"}.issubset(codes)


def test_startup_recovery_reconciles_remote_state_and_reports_mismatch(tmp_path):
    _settings, sessions, audit, alerts, emergency, _kill = services(tmp_path)
    TradeMemoryRepository(sessions).record_execution("trade-1", None, {"tickets": {"position": 99}})
    recovery = StartupRecoveryService(sessions, ModelRegistry(sessions), emergency, alerts, audit)
    class Remote:
        def positions(self): return []
        def pending_orders(self): return []
        def deal_history(self): return []
    result = recovery.recover(Remote())
    assert result["mt5_reconciled"] is True and result["mismatches"][0]["position_ticket"] == 99
    assert "STATE_MISMATCH" in {item["code"] for item in alerts.recent()}


def test_invalid_ai_output_is_blocked_before_risk_execution():
    invalid = TradeProposal(ProposalAction.LONG, 1.5, "Brain-v1", "phase3-v1", datetime.now(timezone.utc))
    with pytest.raises(ValueError): ProposalSafetyValidator().validate(invalid)
