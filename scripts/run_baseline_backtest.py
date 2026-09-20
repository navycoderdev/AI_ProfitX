"""Run one deterministic engineering baseline against persisted immutable raw bars."""
import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from backtesting.engine import BacktestEngine
from backtesting.types import BacktestConfig, CostAssumptions
from config.settings import get_settings
from data.storage import RawMarketDataRepository
from database.models import BacktestRun
from database.session import initialize_database
from strategies.baselines import BreakoutBaseline, MeanReversionBaseline, MovingAverageTrendBaseline


STRATEGIES = {"ma_trend": MovingAverageTrendBaseline, "breakout": BreakoutBaseline, "mean_reversion": MeanReversionBaseline}


def utc(value: str) -> datetime: return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
def serialise(value): return json.loads(json.dumps(value, default=str))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True); parser.add_argument("--timeframe", required=True)
    parser.add_argument("--strategy", choices=STRATEGIES, default="ma_trend")
    parser.add_argument("--from", dest="start", required=True); parser.add_argument("--to", dest="end", required=True)
    parser.add_argument("--starting-cash", type=float, default=10_000); parser.add_argument("--position-size", type=float, default=1_000)
    parser.add_argument("--commission-per-unit", type=float, default=0.0); parser.add_argument("--slippage-points", type=float, default=1.0)
    parser.add_argument("--point-size", type=float, default=0.00001)
    args = parser.parse_args(); sessions = initialize_database(get_settings())
    bars = [bar for bar in RawMarketDataRepository(sessions).bars_as_of(args.symbol, args.timeframe, utc(args.end)) if bar.timestamp >= utc(args.start)]
    strategy = STRATEGIES[args.strategy]()
    config = BacktestConfig(args.symbol.upper(), args.timeframe.upper(), args.starting_cash, args.position_size,
        strategy_version=strategy.version, costs=CostAssumptions(args.point_size, args.commission_per_unit, args.slippage_points))
    report = BacktestEngine().run(bars, strategy, config)
    payload = serialise(report.as_dict())
    with sessions() as session:
        record = BacktestRun(strategy_version=strategy.version, symbol=args.symbol.upper(), timeframe=args.timeframe.upper(),
            data_version=config.data_version, configuration=serialise(asdict(config)), results=payload)
        session.add(record); session.commit(); session.refresh(record)
    Path("reports/backtests").mkdir(parents=True, exist_ok=True)
    (Path("reports/backtests") / f"{record.run_id}.json").write_text(json.dumps({"run_id": record.run_id, **payload}, indent=2), encoding="utf-8")
    print(json.dumps({"run_id": record.run_id, "metrics": report.metrics, "bars": len(bars)}, default=str, indent=2))


if __name__ == "__main__": main()
