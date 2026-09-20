"""experience memory

Revision ID: 0003_experience_memory
Revises: 0002_market_data_features
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_experience_memory"
down_revision = "0002_market_data_features"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("market_snapshots", sa.Column("id", sa.Integer, primary_key=True), sa.Column("snapshot_id", sa.String(36), nullable=False, unique=True), sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False), sa.Column("symbol", sa.String(40), nullable=False), sa.Column("timeframe", sa.String(10), nullable=False), sa.Column("regime", sa.String(50)), sa.Column("spread", sa.Float), sa.Column("volatility", sa.Float), sa.Column("session", sa.String(30)), sa.Column("market_data", sa.JSON, nullable=False), sa.Column("account_state", sa.JSON, nullable=False), sa.Column("existing_exposure", sa.JSON, nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("decision_memories", sa.Column("id", sa.Integer, primary_key=True), sa.Column("decision_id", sa.String(36), nullable=False, unique=True), sa.Column("trade_id", sa.String(36)), sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False), sa.Column("symbol", sa.String(40), nullable=False), sa.Column("timeframe", sa.String(10), nullable=False), sa.Column("environment", sa.String(20), nullable=False), sa.Column("strategy_version", sa.String(100), nullable=False), sa.Column("model_version", sa.String(100), nullable=False), sa.Column("feature_version", sa.String(40), nullable=False), sa.Column("feature_snapshot_id", sa.String(36)), sa.Column("market_snapshot_id", sa.String(36), nullable=False), sa.Column("direction", sa.String(12), nullable=False), sa.Column("confidence", sa.Float), sa.Column("proposed_entry", sa.Float), sa.Column("proposed_stop", sa.Float), sa.Column("proposed_target", sa.Float), sa.Column("decision_metadata", sa.JSON, nullable=False), sa.Column("risk_metadata", sa.JSON, nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("trade_memories", sa.Column("id", sa.Integer, primary_key=True), sa.Column("trade_id", sa.String(36), nullable=False, unique=True), sa.Column("decision_id", sa.String(36)), sa.Column("execution", sa.JSON, nullable=False), sa.Column("outcome", sa.JSON, nullable=False), sa.Column("label", sa.JSON, nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("trade_memory_events", sa.Column("id", sa.Integer, primary_key=True), sa.Column("trade_id", sa.String(36), nullable=False), sa.Column("event_type", sa.String(50), nullable=False), sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False), sa.Column("payload", sa.JSON, nullable=False))


def downgrade() -> None:
    op.drop_table("trade_memory_events"); op.drop_table("trade_memories"); op.drop_table("decision_memories"); op.drop_table("market_snapshots")
