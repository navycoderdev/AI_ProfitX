from fastapi import APIRouter, Request

router = APIRouter()

@router.get("/health")
def health(request: Request) -> dict:
    return request.app.state.health_service.report()

@router.get("/runtime")
def runtime(request: Request) -> dict:
    settings = request.app.state.settings
    return {"environment": settings.app_env.value, "mode": settings.trading_mode.value,
            "live_execution_enabled": settings.trading_mode.value == "LIVE" and settings.allow_live_trading}


@router.get("/control/overview")
def control_overview(request: Request) -> dict: return request.app.state.control_center.overview()

@router.get("/control/live-market")
def control_market(request: Request) -> list[dict]: return request.app.state.control_center.live_market()

@router.get("/control/ai-brain")
def control_ai_brain(request: Request) -> dict: return request.app.state.control_center.ai_brain()

@router.get("/control/positions")
def control_positions(request: Request) -> list[dict]: return request.app.state.control_center.positions_view()

@router.get("/control/trade-memory")
def control_trade_memory(request: Request, limit: int = 100) -> list[dict]: return request.app.state.control_center.trade_memory(limit)

@router.get("/control/model-lab")
def control_model_lab(request: Request) -> dict: return request.app.state.control_center.model_lab()

@router.get("/control/risk-center")
def control_risk(request: Request) -> dict: return request.app.state.control_center.risk_center()

@router.get("/control/system-health")
def control_system_health(request: Request) -> dict: return request.app.state.control_center.system_health()

@router.get("/control/audit-log")
def control_audit(request: Request, limit: int = 200) -> list[dict]: return request.app.state.control_center.audit_log(limit)

@router.get("/control/research-data")
def control_research_data(request: Request) -> dict: return request.app.state.control_center.research_data()

@router.get("/control/backtest-runs")
def control_backtest_runs(request: Request) -> list[dict]: return request.app.state.control_center.backtest_runs()


@router.post("/control/emergency-stop")
def emergency_stop(request: Request, payload: dict) -> dict:
    return request.app.state.emergency_stop.activate(str(payload.get("operator", "")), str(payload.get("reason", "")))


@router.post("/control/emergency-reactivate")
def emergency_reactivate(request: Request, payload: dict) -> dict:
    return request.app.state.emergency_stop.reactivate(str(payload.get("operator", "")), str(payload.get("recovery_procedure", "")))
