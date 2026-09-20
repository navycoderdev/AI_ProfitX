from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from data.types import MarketBar, MarketTick
from database.models import RawMarketBar, RawMarketTick


class RawMarketDataRepository:
    """Append-only raw store. Existing provider observations can never be updated."""
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def append_bars(self, bars: list[MarketBar]) -> int:
        inserted = 0
        with self.sessions() as session:
            for bar in bars:
                exists = session.scalar(select(RawMarketBar.id).where(RawMarketBar.source == bar.source,
                    RawMarketBar.symbol == bar.symbol, RawMarketBar.timeframe == bar.timeframe,
                    RawMarketBar.timestamp == bar.timestamp))
                if exists is None:
                    session.add(RawMarketBar(**bar.as_dict()))
                    inserted += 1
            session.commit()
        return inserted

    def append_tick(self, tick: MarketTick) -> bool:
        with self.sessions() as session:
            exists = session.scalar(select(RawMarketTick.id).where(RawMarketTick.source == tick.source,
                RawMarketTick.symbol == tick.symbol, RawMarketTick.timestamp == tick.timestamp,
                RawMarketTick.bid == tick.bid, RawMarketTick.ask == tick.ask))
            if exists is not None:
                return False
            session.add(RawMarketTick(source=tick.source, symbol=tick.symbol, timestamp=tick.timestamp,
                bid=tick.bid, ask=tick.ask, last=tick.last, volume=tick.volume))
            session.commit()
            return True

    def count_bars(self) -> int:
        """Read-only count used by operational integrity checks."""
        with self.sessions() as session:
            return int(session.scalar(select(func.count(RawMarketBar.id))) or 0)

    def bars_as_of(self, symbol: str, timeframe: str, decision_at: datetime) -> list[MarketBar]:
        cutoff = decision_at.astimezone(timezone.utc)
        with self.sessions() as session:
            rows = session.scalars(select(RawMarketBar).where(RawMarketBar.symbol == symbol.upper(),
                RawMarketBar.timeframe == timeframe.upper(), RawMarketBar.timestamp <= cutoff).order_by(RawMarketBar.timestamp)).all()
        return [MarketBar(row.source, row.symbol, row.timeframe,
                row.timestamp if row.timestamp.tzinfo else row.timestamp.replace(tzinfo=timezone.utc), row.open, row.high, row.low, row.close,
                row.volume, row.tick_volume, row.spread) for row in rows]
