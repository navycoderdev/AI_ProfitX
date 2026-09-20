"""Report only persisted raw MT5 candle coverage from the configured database."""
from sqlalchemy import func, select

from config.settings import get_settings
from database.models import RawMarketBar
from database.session import initialize_database


def main() -> None:
    sessions = initialize_database(get_settings())
    with sessions() as session:
        rows = session.execute(select(RawMarketBar.symbol, RawMarketBar.timeframe, func.count(RawMarketBar.id),
            func.min(RawMarketBar.timestamp), func.max(RawMarketBar.timestamp)).group_by(RawMarketBar.symbol, RawMarketBar.timeframe)
        ).all()
    if not rows:
        print("No persisted historical candles.")
        return
    print("Symbol\tTimeframe\tRows\tFirst UTC\tLast UTC")
    for symbol, timeframe, count, first, last in rows:
        print(f"{symbol}\t{timeframe}\t{count}\t{first}\t{last}")


if __name__ == "__main__": main()
