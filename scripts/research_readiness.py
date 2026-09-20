"""Read-only readiness verdict for historical-data/backtest research only."""
import json
from pathlib import Path
from sqlalchemy import func, select
from config.settings import get_settings
from database.models import AuditLog, BacktestRun, DecisionMemory, FeatureSnapshot, ModelVersion, RawMarketBar, RegimeHistory
from database.session import initialize_database

if __name__ == "__main__":
    settings, sessions = get_settings(), initialize_database(get_settings())
    with sessions() as session:
        coverage = session.execute(select(RawMarketBar.symbol, RawMarketBar.timeframe, func.count(RawMarketBar.id)).group_by(RawMarketBar.symbol, RawMarketBar.timeframe)).all()
        count = lambda model: session.scalar(select(func.count()).select_from(model))
        latest = session.scalar(select(BacktestRun).order_by(BacktestRun.created_at.desc()))
        orders = session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "mt5.order.submit"))
    expected = {(symbol, timeframe) for symbol in settings.market_symbols for timeframe in settings.market_timeframes}
    present = {(symbol, timeframe) for symbol, timeframe, _ in coverage}
    aggregate_files = sorted(Path("reports/data_quality").glob("aggregate_*.json"), key=lambda path: path.stat().st_mtime)
    quality_status = json.loads(aggregate_files[-1].read_text(encoding="utf-8"))["overall_status"] if aggregate_files else None
    verdict = ("DATA_PIPELINE_READY" if quality_status == "PASS" else "DATA_PIPELINE_WARNING") if expected <= present and count(BacktestRun) else "DATA_PIPELINE_NOT_READY"
    output = {"verdict": verdict, "mode": settings.trading_mode.value, "live_permission": settings.allow_live_trading,
        "historical_candle_count": sum(row[2] for row in coverage), "quality_status": quality_status, "coverage": [{"symbol": s, "timeframe": t, "count": n} for s,t,n in coverage],
        "backtest_run_count": count(BacktestRun), "latest_backtest": latest.run_id if latest else None,
        "feature_snapshot_count": count(FeatureSnapshot), "regime_count": count(RegimeHistory), "ai_model_count": count(ModelVersion),
        "decision_count": count(DecisionMemory), "mt5_order_submission_count": orders}
    print(json.dumps(output, indent=2, default=str))
