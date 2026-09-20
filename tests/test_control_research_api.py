import asyncio
import json
from datetime import datetime, timezone

import httpx

from app.main import app
from config.settings import Settings
from data.storage import RawMarketDataRepository
from data.types import MarketBar
from database.audit import AuditRepository
from database.models import BacktestRun
from database.session import initialize_database
from models.registry import ModelRegistry
from monitoring.alerts import AlertService
from monitoring.control import EmergencyStopService
from monitoring.control_center import ControlCenter


UTC = timezone.utc


def request(path: str):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get(path)
    return asyncio.run(run())


def isolated_center(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'test.db'}")
    sessions = initialize_database(settings); audit = AuditRepository(sessions)
    center = ControlCenter(settings, sessions, ModelRegistry(sessions, audit), AlertService(sessions),
        EmergencyStopService(sessions, settings, audit), lambda: {"mt5": False, "database": True, "status": "ok"})
    app.state.control_center = center
    return sessions


def write_quality(tmp_path, status="WARNING"):
    folder = tmp_path / "reports" / "data_quality"; folder.mkdir(parents=True)
    payload = {"run_id": "quality-test", "created_at": "2026-01-02T00:00:00+00:00", "overall_status": status,
        "coverage": [{"symbol": "EURUSD", "broker_symbol": "EURUSDm", "timeframe": "M5", "status": status,
            "gaps": [], "outliers": [], "duplicates": 0, "invalid_ohlc": []}]}
    (folder / "aggregate_test.json").write_text(json.dumps(payload), encoding="utf-8")


def test_research_data_api_populated_preserves_warning(tmp_path, monkeypatch):
    sessions = isolated_center(tmp_path, monkeypatch); write_quality(tmp_path, "WARNING")
    RawMarketDataRepository(sessions).append_bars([MarketBar("TEST", "EURUSD", "M5", datetime(2026, 1, 1, tzinfo=UTC), 1, 2, .5, 1.5, 1, 1, 1)])
    response = request("/api/control/research-data")
    assert response.status_code == 200
    body = response.json()
    assert body["total_candles"] == 1 and body["symbols"] == ["EURUSD"]
    assert body["aggregate"]["overall_status"] == "WARNING"
    assert body["coverage"][0]["broker_symbol"] == "EURUSDm"
    assert body["dataset"]["dataset_state"] == "NOT_BUILT" and body["dataset"]["content_hash"] is None


def test_live_shadow_api_reports_empty_persisted_state(tmp_path, monkeypatch):
    isolated_center(tmp_path, monkeypatch)
    response = request("/api/control/live-shadow")
    assert response.status_code == 200
    body = response.json()
    assert body["service"]["status"] == "NOT_STARTED"
    assert body["counts"]["total"] == body["counts"]["orders_submitted"] == 0
    assert set(body["per_symbol"]) == {"EURUSD", "GBPUSD", "USDJPY", "XAUUSD"}
    assert body["decisions"] == []


def test_research_data_api_exposes_quality_reason_codes(tmp_path, monkeypatch):
    sessions = isolated_center(tmp_path, monkeypatch); write_quality(tmp_path, "WARNING")
    quality_file = tmp_path / "reports" / "data_quality" / "aggregate_test.json"
    payload = json.loads(quality_file.read_text()); payload["coverage"][0]["reason_codes"] = ["POSSIBLE_DATA_GAP"]
    quality_file.write_text(json.dumps(payload))
    RawMarketDataRepository(sessions).append_bars([MarketBar("TEST", "EURUSD", "M5", datetime(2026, 1, 1, tzinfo=UTC), 1, 2, .5, 1.5)])
    assert request("/api/control/research-data").json()["coverage"][0]["reason_codes"] == ["POSSIBLE_DATA_GAP"]


def test_research_data_api_empty_and_fail_quality_are_real(tmp_path, monkeypatch):
    isolated_center(tmp_path, monkeypatch)
    assert request("/api/control/research-data").json()["coverage"] == []
    write_quality(tmp_path, "FAIL")
    body = request("/api/control/research-data").json()
    assert body["total_candles"] == 0 and body["aggregate"]["overall_status"] == "FAIL"


def test_backtest_runs_api_empty_single_and_latest_first(tmp_path, monkeypatch):
    sessions = isolated_center(tmp_path, monkeypatch)
    assert request("/api/control/backtest-runs").json() == []
    with sessions() as session:
        session.add_all([
            BacktestRun(run_id="older", strategy_version="baseline-v1", symbol="EURUSD", timeframe="M5", data_version="raw-v1", configuration={"period": "a"}, results={"metrics": {"trade_count": 1, "net_pnl": 2.0, "profit_factor": 1.1, "maximum_drawdown": .1, "transaction_costs": .2, "slippage_impact": .1}}),
            BacktestRun(run_id="newer", strategy_version="baseline-v1", symbol="GBPUSD", timeframe="M15", data_version="raw-v1", configuration={"period": "b"}, results={"metrics": {"trade_count": 2, "net_pnl": -1.0}}),
        ]); session.commit()
    body = request("/api/control/backtest-runs").json()
    assert [item["run_id"] for item in body] == ["newer", "older"]
    assert body[1]["metrics"]["transaction_costs"] == .2
