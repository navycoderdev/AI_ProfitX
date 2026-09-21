"""Operational data ingestion and quality-report helpers for MT5 historical research."""

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
import json

from data.collectors import HistoricalDataCollector
from data.normalizer import DataNormalizer
from data.quality import DataQualityEngine
from data.storage import RawMarketDataRepository
from data.types import MarketBar
from mt5.services import MT5MarketDataService, MT5SymbolService


def resolve_broker_symbol(symbols: MT5SymbolService, canonical_symbol: str) -> str:
    """Resolve a configured canonical FX pair against broker suffix/prefix variants."""
    canonical = canonical_symbol.upper()
    try:
        symbols.ensure_available(canonical)
        return canonical
    except Exception:
        available = symbols.mt5.symbols_get() or ()
        names = [getattr(item, "name", None) or item._asdict().get("name") for item in available]
        matches = [name for name in names if name and (name.upper().startswith(canonical) or name.upper().endswith(canonical))]
        if not matches:
            raise ValueError(f"No broker symbol resolves canonical symbol {canonical}.")
        if len(matches) != 1:
            raise ValueError(f"Ambiguous broker symbols for {canonical}: {sorted(matches)}")
        resolved = matches[0]
        symbols.ensure_available(resolved)
        return resolved


class ChunkedHistoricalIngestor:
    def __init__(self, market: MT5MarketDataService, repository: RawMarketDataRepository) -> None:
        self.market, self.repository = market, repository
        self.normalizer, self.symbols = DataNormalizer(), MT5SymbolService(market.manager, market.settings, market.audit)

    def ingest(self, canonical_symbol: str, timeframe_name: str, start: datetime, end: datetime,
               chunk_days: int = 30) -> dict:
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ValueError("Ingestion dates must be ordered, timezone-aware UTC timestamps.")
        supported = self.symbols.supported_timeframes()
        if timeframe_name.upper() not in supported:
            raise ValueError(f"Unsupported timeframe: {timeframe_name}")
        broker_symbol = resolve_broker_symbol(self.symbols, canonical_symbol)
        cursor, received, inserted, errors, first, last = start, 0, 0, [], None, None
        chunks = []
        while cursor < end:
            chunk_end = min(cursor + timedelta(days=chunk_days), end)
            try:
                payloads = self.market.rates(broker_symbol, supported[timeframe_name.upper()], cursor, chunk_end)
                bars = [self.normalizer.bar("MT5", canonical_symbol, timeframe_name, payload) for payload in payloads]
                count = self.repository.append_bars(bars)
                received += len(bars); inserted += count
                if bars:
                    first = min(first, bars[0].timestamp) if first else bars[0].timestamp
                    last = max(last, bars[-1].timestamp) if last else bars[-1].timestamp
                chunks.append({"start": cursor.isoformat(), "end": chunk_end.isoformat(), "received": len(bars), "inserted": count})
            except Exception as exc:
                errors.append({"start": cursor.isoformat(), "end": chunk_end.isoformat(), "error": str(exc)})
            cursor = chunk_end
        return {"run_id": str(uuid4()), "canonical_symbol": canonical_symbol.upper(), "broker_symbol": broker_symbol,
                "timeframe": timeframe_name.upper(), "requested_start": start.isoformat(), "requested_end": end.isoformat(),
                "first_candle": first.isoformat() if first else None, "last_candle": last.isoformat() if last else None,
                "received": received, "inserted": inserted, "duplicates_skipped": received - inserted,
                "errors": errors, "chunks": chunks}


def quality_report(repository: RawMarketDataRepository, symbol: str, timeframe: str, start: datetime, end: datetime,
                   report_dir: Path, source: str | None = None) -> dict:
    bars = [bar for bar in repository.bars_as_of(symbol, timeframe, end, source=source) if bar.timestamp >= start]
    base = DataQualityEngine().report(symbol, timeframe, bars)
    invalid_ohlc = [bar.timestamp.isoformat() for bar in bars if min(bar.open, bar.close) < bar.low or max(bar.open, bar.close) > bar.high or bar.high < bar.low or min(bar.open, bar.high, bar.low, bar.close) <= 0]
    classified_gaps = []
    for gap in base.timestamp_gaps:
        after = datetime.fromisoformat(gap["after"])
        before = datetime.fromisoformat(gap["before"])
        classified_gaps.append({**gap, "classification": "EXPECTED_MARKET_CLOSURE" if after.weekday() == 4 or before.weekday() == 6 else "POSSIBLE_DATA_GAP"})
    from research.governance import QualityReasonCode, quality_status, reason_codes_from_quality
    provisional = {**asdict(base), "invalid_ohlc": invalid_ohlc, "gaps": classified_gaps}
    reason_codes = reason_codes_from_quality(provisional)
    if not bars: reason_codes.append(QualityReasonCode.BROKER_HISTORY_UNAVAILABLE.value)
    status = "FAIL" if not bars else quality_status({QualityReasonCode(code) for code in reason_codes})
    result = {**provisional, "source": source, "reason_codes": sorted(set(reason_codes)), "status": status,
              "range": {"start": start.isoformat(), "end": end.isoformat()}}
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{symbol.upper()}_{timeframe.upper()}_{uuid4()}.json").write_text(json.dumps(result, default=str, indent=2), encoding="utf-8")
    return result


def aggregate_quality(repository: RawMarketDataRepository, report_dir: Path) -> dict:
    """Produce a reproducible coverage/quality snapshot from immutable persisted bars."""
    from sqlalchemy import func, select
    from database.models import RawMarketBar
    with repository.sessions() as session:
        coverage = session.execute(select(RawMarketBar.symbol, RawMarketBar.timeframe, func.count(RawMarketBar.id),
            func.min(RawMarketBar.timestamp), func.max(RawMarketBar.timestamp)).group_by(RawMarketBar.symbol, RawMarketBar.timeframe)).all()
    rows = []
    for symbol, timeframe, count, first, last in coverage:
        start = first if first.tzinfo else first.replace(tzinfo=timezone.utc)
        end = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
        quality = quality_report(repository, symbol, timeframe, start, end, report_dir)
        rows.append({"symbol": symbol, "broker_symbol": symbol, "timeframe": timeframe, "row_count": count,
                     "first_timestamp": start.isoformat(), "last_timestamp": end.isoformat(), **quality})
    statuses = [row["status"] for row in rows]
    overall = "FAIL" if "FAIL" in statuses else "WARNING" if "WARNING" in statuses else "PASS"
    run_id = str(uuid4()); aggregate = {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(),
        "overall_status": overall, "coverage": rows}
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"aggregate_{run_id}.json").write_text(json.dumps(aggregate, indent=2, default=str), encoding="utf-8")
    markdown = [f"# Aggregate Data Quality — {run_id}", "", f"Overall status: **{overall}**", "", "| Symbol | TF | Rows | First UTC | Last UTC | Status | Possible gaps |", "|---|---|---:|---|---|---|---:|"]
    markdown += [f"| {row['symbol']} | {row['timeframe']} | {row['row_count']} | {row['first_timestamp']} | {row['last_timestamp']} | {row['status']} | {sum(g['classification'] != 'EXPECTED_MARKET_CLOSURE' for g in row['gaps'])} |" for row in rows]
    (report_dir / f"aggregate_{run_id}.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
    return aggregate
