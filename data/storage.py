from datetime import datetime, timezone

from sqlalchemy import func, insert, select
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

    def append_bars_bulk(self, bars: list[MarketBar]) -> int:
        """Append a bounded provider batch, rejecting changed observations."""
        if not bars:
            return 0
        keys = [(bar.source, bar.symbol, bar.timeframe, bar.timestamp) for bar in bars]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate keys in raw broker batch.")
        source, symbol, timeframe = bars[0].source, bars[0].symbol, bars[0].timeframe
        if any(key[:3] != (source, symbol, timeframe) for key in keys):
            raise ValueError("Raw broker batch must use one source, symbol and timeframe.")
        with self.sessions() as session:
            prior = list(session.scalars(select(RawMarketBar).where(
                RawMarketBar.source == source, RawMarketBar.symbol == symbol,
                RawMarketBar.timeframe == timeframe,
                RawMarketBar.timestamp.in_([bar.timestamp for bar in bars]))))
            existing = {row.timestamp.replace(tzinfo=timezone.utc): row for row in prior}
            missing = []
            for bar in bars:
                old = existing.get(bar.timestamp.astimezone(timezone.utc))
                if old is None:
                    missing.append(bar.as_dict())
                elif any(getattr(old, field) != getattr(bar, field) for field in
                         ("open", "high", "low", "close", "volume", "tick_volume", "spread")):
                    raise ValueError("Changed immutable raw broker observation.")
            if missing:
                session.execute(insert(RawMarketBar), missing)
                session.commit()
            return len(missing)

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

    def bars_as_of(self, symbol: str, timeframe: str, decision_at: datetime,
                   source: str | None = None) -> list[MarketBar]:
        cutoff = decision_at.astimezone(timezone.utc)
        with self.sessions() as session:
            query = select(RawMarketBar).where(RawMarketBar.symbol == symbol.upper(),
                RawMarketBar.timeframe == timeframe.upper(), RawMarketBar.timestamp <= cutoff)
            if source is not None:
                query = query.where(RawMarketBar.source == source)
            rows = session.scalars(query.order_by(RawMarketBar.timestamp)).all()
        return [MarketBar(row.source, row.symbol, row.timeframe,
                row.timestamp if row.timestamp.tzinfo else row.timestamp.replace(tzinfo=timezone.utc), row.open, row.high, row.low, row.close,
                row.volume, row.tick_volume, row.spread) for row in rows]
