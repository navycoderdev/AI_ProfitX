"""market data and feature storage

Revision ID: 0002_market_data_features
Revises: 0001_foundation
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_market_data_features"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("raw_market_bars", sa.Column("id", sa.Integer, primary_key=True), sa.Column("source", sa.String(30), nullable=False), sa.Column("symbol", sa.String(40), nullable=False), sa.Column("timeframe", sa.String(10), nullable=False), sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False), sa.Column("open", sa.Float, nullable=False), sa.Column("high", sa.Float, nullable=False), sa.Column("low", sa.Float, nullable=False), sa.Column("close", sa.Float, nullable=False), sa.Column("volume", sa.Float), sa.Column("tick_volume", sa.Float), sa.Column("spread", sa.Float), sa.Column("received_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("source", "symbol", "timeframe", "timestamp", name="uq_raw_bar_observation"))
    op.create_index("ix_raw_market_bars_symbol", "raw_market_bars", ["symbol"])
    op.create_index("ix_raw_market_bars_timeframe", "raw_market_bars", ["timeframe"])
    op.create_index("ix_raw_market_bars_timestamp", "raw_market_bars", ["timestamp"])
    op.create_table("raw_market_ticks", sa.Column("id", sa.Integer, primary_key=True), sa.Column("source", sa.String(30), nullable=False), sa.Column("symbol", sa.String(40), nullable=False), sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False), sa.Column("bid", sa.Float, nullable=False), sa.Column("ask", sa.Float, nullable=False), sa.Column("last", sa.Float), sa.Column("volume", sa.Float), sa.Column("received_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("source", "symbol", "timestamp", "bid", "ask", name="uq_raw_tick_observation"))
    op.create_index("ix_raw_market_ticks_symbol", "raw_market_ticks", ["symbol"])
    op.create_index("ix_raw_market_ticks_timestamp", "raw_market_ticks", ["timestamp"])
    op.create_table("feature_definitions", sa.Column("id", sa.Integer, primary_key=True), sa.Column("name", sa.String(100), nullable=False), sa.Column("version", sa.String(40), nullable=False), sa.Column("specification", sa.JSON, nullable=False), sa.Column("active", sa.Boolean, nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("name", "version", name="uq_feature_definition_version"))
    op.create_table("feature_snapshots", sa.Column("id", sa.Integer, primary_key=True), sa.Column("snapshot_id", sa.String(36), nullable=False, unique=True), sa.Column("symbol", sa.String(40), nullable=False), sa.Column("timeframe", sa.String(10), nullable=False), sa.Column("decision_at", sa.DateTime(timezone=True), nullable=False), sa.Column("feature_version", sa.String(40), nullable=False), sa.Column("raw_data_cutoff", sa.DateTime(timezone=True), nullable=False), sa.Column("values", sa.JSON, nullable=False), sa.Column("context", sa.JSON, nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_feature_snapshot_lookup", "feature_snapshots", ["symbol", "timeframe", "decision_at"])


def downgrade() -> None:
    op.drop_table("feature_snapshots")
    op.drop_table("feature_definitions")
    op.drop_table("raw_market_ticks")
    op.drop_table("raw_market_bars")
