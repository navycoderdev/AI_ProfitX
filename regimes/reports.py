from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from database.models import DecisionMemory, MarketSnapshot, TradeMemory


class HistoricalRegimeReport:
    """Segments recorded decision/trade outcomes by the regime saved at decision time."""
    def __init__(self, sessions: sessionmaker[Session]) -> None: self.sessions = sessions
    def strategy_performance(self, strategy_version: str | None = None, model_version: str | None = None) -> dict:
        with self.sessions() as session:
            decisions = list(session.scalars(select(DecisionMemory)))
            snapshots = {row.snapshot_id: row for row in session.scalars(select(MarketSnapshot))}
            trades = {row.trade_id: row for row in session.scalars(select(TradeMemory))}
        grouped: dict[str, dict] = defaultdict(lambda: {"decisions": 0, "no_trade_decisions": 0, "executed_trades": 0, "net_pnl_after_costs": 0.0, "wins": 0, "losses": 0})
        for decision in decisions:
            if strategy_version and decision.strategy_version != strategy_version: continue
            if model_version and decision.model_version != model_version: continue
            snapshot = snapshots.get(decision.market_snapshot_id); label = snapshot.regime if snapshot and snapshot.regime else "UNCERTAIN"
            row = grouped[label]; row["decisions"] += 1
            if decision.direction == "NO_TRADE": row["no_trade_decisions"] += 1
            trade = trades.get(decision.trade_id) if decision.trade_id else None
            pnl = trade.outcome.get("pnl_after_costs") if trade else None
            if pnl is not None:
                row["executed_trades"] += 1; row["net_pnl_after_costs"] += float(pnl)
                if pnl > 0: row["wins"] += 1
                elif pnl < 0: row["losses"] += 1
        for row in grouped.values():
            row["win_rate"] = row["wins"] / row["executed_trades"] if row["executed_trades"] else None
        return dict(grouped)
