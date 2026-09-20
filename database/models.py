from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), unique=True, default=lambda: str(uuid4()))
    action: Mapped[str] = mapped_column(String(100), index=True)
    environment: Mapped[str] = mapped_column(String(20), index=True)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc), index=True)


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    stage: Mapped[str] = mapped_column(String(30), index=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc))


class RawMarketBar(Base):
    """Immutable provider observation. Duplicate inserts are ignored by the repository."""
    __tablename__ = "raw_market_bars"
    __table_args__ = (UniqueConstraint("source", "symbol", "timeframe", "timestamp", name="uq_raw_bar_observation"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(30), default="MT5")
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    timeframe: Mapped[str] = mapped_column(String(10), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    tick_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    spread: Mapped[float | None] = mapped_column(Float, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class RawMarketTick(Base):
    __tablename__ = "raw_market_ticks"
    __table_args__ = (UniqueConstraint("source", "symbol", "timestamp", "bid", "ask", name="uq_raw_tick_observation"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(30), default="MT5")
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    bid: Mapped[float] = mapped_column(Float)
    ask: Mapped[float] = mapped_column(Float)
    last: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class DatasetQuarantineMask(Base):
    __tablename__ = "dataset_quarantine_masks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mask_id: Mapped[str] = mapped_column(String(36), unique=True, default=lambda: str(uuid4()))
    policy_version: Mapped[str] = mapped_column(String(50), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    timeframe: Mapped[str] = mapped_column(String(10), index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    state: Mapped[str] = mapped_column(String(30))
    reason_codes: Mapped[list] = mapped_column(JSON, default=list)
    source_quality_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class DatasetManifest(Base):
    """Pre-build/build metadata. A NOT_BUILT record never represents a dataset."""
    __tablename__ = "dataset_manifests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(36), unique=True, default=lambda: str(uuid4()))
    dataset_version: Mapped[str] = mapped_column(String(80), index=True)
    build_id: Mapped[str | None] = mapped_column(String(36), unique=True, nullable=True)
    state: Mapped[str] = mapped_column(String(30), index=True)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    symbols: Mapped[list | None] = mapped_column(JSON, nullable=True)
    source_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_timeframe: Mapped[str] = mapped_column(String(10))
    context_timeframes: Mapped[list] = mapped_column(JSON, default=list)
    feature_set_version: Mapped[str] = mapped_column(String(50))
    quality_policy_version: Mapped[str] = mapped_column(String(50))
    quarantine_policy_version: Mapped[str] = mapped_column(String(50))
    alignment_policy_version: Mapped[str] = mapped_column(String(80))
    split_policy_version: Mapped[str] = mapped_column(String(50))
    candidate_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usable_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    excluded_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quarantined_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    train_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    oos_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason_code_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DatasetRow(Base):
    """Immutable Dataset-v1 membership record; feature values remain in FeatureSnapshot."""
    __tablename__ = "dataset_rows"
    __table_args__ = (UniqueConstraint("dataset_id", "feature_snapshot_id", name="uq_dataset_row_snapshot"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(36), index=True)
    feature_snapshot_id: Mapped[str] = mapped_column(String(36), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    decision_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    split: Mapped[str] = mapped_column(String(20), index=True)
    exclusion_codes: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class BacktestRun(Base):
    """Immutable operational record for a reproducible historical backtest."""
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), unique=True, default=lambda: str(uuid4()))
    strategy_version: Mapped[str] = mapped_column(String(100), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    timeframe: Mapped[str] = mapped_column(String(10), index=True)
    data_version: Mapped[str] = mapped_column(String(80))
    configuration: Mapped[dict] = mapped_column(JSON, default=dict)
    results: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class FeatureDefinition(Base):
    __tablename__ = "feature_definitions"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_feature_definition_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(40), index=True)
    specification: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class FeatureSnapshot(Base):
    __tablename__ = "feature_snapshots"
    __table_args__ = (Index("ix_feature_snapshot_lookup", "symbol", "timeframe", "decision_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(String(36), unique=True, default=lambda: str(uuid4()))
    symbol: Mapped[str] = mapped_column(String(40))
    timeframe: Mapped[str] = mapped_column(String(10))
    decision_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    feature_version: Mapped[str] = mapped_column(String(40))
    raw_data_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    values: Mapped[dict] = mapped_column(JSON, default=dict)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(String(36), unique=True, default=lambda: str(uuid4()))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    timeframe: Mapped[str] = mapped_column(String(10))
    regime: Mapped[str | None] = mapped_column(String(50), nullable=True)
    spread: Mapped[float | None] = mapped_column(Float, nullable=True)
    volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    session: Mapped[str | None] = mapped_column(String(30), nullable=True)
    market_data: Mapped[dict] = mapped_column(JSON, default=dict)
    account_state: Mapped[dict] = mapped_column(JSON, default=dict)
    existing_exposure: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class DecisionMemory(Base):
    __tablename__ = "decision_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    decision_id: Mapped[str] = mapped_column(String(36), unique=True, default=lambda: str(uuid4()))
    trade_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    timeframe: Mapped[str] = mapped_column(String(10))
    environment: Mapped[str] = mapped_column(String(20))
    strategy_version: Mapped[str] = mapped_column(String(100))
    model_version: Mapped[str] = mapped_column(String(100))
    feature_version: Mapped[str] = mapped_column(String(40))
    feature_snapshot_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    market_snapshot_id: Mapped[str] = mapped_column(String(36), index=True)
    direction: Mapped[str] = mapped_column(String(12))
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    proposed_entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    proposed_stop: Mapped[float | None] = mapped_column(Float, nullable=True)
    proposed_target: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    risk_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class TradeMemory(Base):
    __tablename__ = "trade_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    decision_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    execution: Mapped[dict] = mapped_column(JSON, default=dict)
    outcome: Mapped[dict] = mapped_column(JSON, default=dict)
    label: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ShadowDecision(Base):
    """Forward observation only; never an execution or TradeMemory record."""
    __tablename__ = "shadow_decisions"
    __table_args__ = (UniqueConstraint("model_version", "symbol", "decision_at", name="uq_shadow_model_symbol_time"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    decision_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    decision_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    timeframe: Mapped[str] = mapped_column(String(10), default="M5")
    model_version: Mapped[str] = mapped_column(String(100))
    artifact_hash: Mapped[str] = mapped_column(String(64))
    dataset_version: Mapped[str] = mapped_column(String(80))
    dataset_hash: Mapped[str] = mapped_column(String(64))
    feature_set_version: Mapped[str] = mapped_column(String(50))
    feature_snapshot_id: Mapped[str] = mapped_column(String(36))
    raw_data_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    probabilities: Mapped[dict] = mapped_column(JSON)
    raw_prediction: Mapped[str] = mapped_column(String(12))
    final_decision: Mapped[str] = mapped_column(String(12))
    confidence: Mapped[float] = mapped_column(Float)
    confidence_threshold: Mapped[float] = mapped_column(Float)
    market_context: Mapped[dict] = mapped_column(JSON, default=dict)
    risk_status: Mapped[str] = mapped_column(String(12))
    risk_reason_codes: Mapped[list] = mapped_column(JSON, default=list)
    entry_reference: Mapped[float | None] = mapped_column(Float, nullable=True)
    proposed_stop: Mapped[float | None] = mapped_column(Float, nullable=True)
    proposed_target: Mapped[float | None] = mapped_column(Float, nullable=True)
    environment: Mapped[str] = mapped_column(String(20), default="SHADOW")
    order_submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ShadowOutcome(Base):
    __tablename__ = "shadow_outcomes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    decision_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    horizon_bar_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entry_close: Mapped[float] = mapped_column(Float)
    horizon_close: Mapped[float] = mapped_column(Float)
    realized_label: Mapped[str] = mapped_column(String(12))
    hypothetical_pnl_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class TradeMemoryEvent(Base):
    __tablename__ = "trade_memory_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class RegimeHistory(Base):
    __tablename__ = "regime_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    regime_id: Mapped[str] = mapped_column(String(36), unique=True, default=lambda: str(uuid4()))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    timeframe: Mapped[str] = mapped_column(String(10), index=True)
    label: Mapped[str] = mapped_column(String(30), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    measurements: Mapped[dict] = mapped_column(JSON, default=dict)
    feature_version: Mapped[str] = mapped_column(String(40))
    regime_model_version: Mapped[str] = mapped_column(String(100))
    previous_label: Mapped[str | None] = mapped_column(String(30), nullable=True)
    transition: Mapped[bool] = mapped_column(Boolean, default=False)
    decision_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ModelInferenceLog(Base):
    __tablename__ = "model_inference_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inference_id: Mapped[str] = mapped_column(String(36), unique=True, default=lambda: str(uuid4()))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    model_version: Mapped[str] = mapped_column(String(100), index=True)
    feature_version: Mapped[str] = mapped_column(String(40))
    feature_snapshot_id: Mapped[str] = mapped_column(String(36), index=True)
    regime: Mapped[str] = mapped_column(String(30))
    portfolio_context: Mapped[dict] = mapped_column(JSON, default=dict)
    model_input: Mapped[dict] = mapped_column(JSON, default=dict)
    model_output: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SystemControlState(Base):
    __tablename__ = "system_control_state"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class OperationalAlert(Base):
    __tablename__ = "operational_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alert_id: Mapped[str] = mapped_column(String(36), unique=True, default=lambda: str(uuid4()))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    code: Mapped[str] = mapped_column(String(60), index=True)
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
