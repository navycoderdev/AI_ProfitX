"""Separate forward Shadow observations from executed trade memory.

Revision ID: 0007_shadow_observations
Revises: 0006_operations_control
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_shadow_observations"
down_revision = "0006_operations_control"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("shadow_decisions",
        sa.Column("id", sa.Integer, primary_key=True), sa.Column("decision_id", sa.String(64), nullable=False),
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=False), sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("timeframe", sa.String(10), nullable=False), sa.Column("model_version", sa.String(100), nullable=False),
        sa.Column("artifact_hash", sa.String(64), nullable=False), sa.Column("dataset_version", sa.String(80), nullable=False),
        sa.Column("dataset_hash", sa.String(64), nullable=False), sa.Column("feature_set_version", sa.String(50), nullable=False),
        sa.Column("feature_snapshot_id", sa.String(36), nullable=False),
        sa.Column("raw_data_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("probabilities", sa.JSON, nullable=False), sa.Column("raw_prediction", sa.String(12), nullable=False),
        sa.Column("final_decision", sa.String(12), nullable=False), sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("confidence_threshold", sa.Float, nullable=False), sa.Column("market_context", sa.JSON, nullable=False),
        sa.Column("risk_status", sa.String(12), nullable=False), sa.Column("risk_reason_codes", sa.JSON, nullable=False),
        sa.Column("entry_reference", sa.Float), sa.Column("proposed_stop", sa.Float), sa.Column("proposed_target", sa.Float),
        sa.Column("environment", sa.String(20), nullable=False), sa.Column("order_submitted", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("model_version", "symbol", "decision_at", name="uq_shadow_model_symbol_time"))
    op.create_index("ix_shadow_decisions_decision_id", "shadow_decisions", ["decision_id"], unique=True)
    op.create_index("ix_shadow_decisions_decision_at", "shadow_decisions", ["decision_at"])
    op.create_index("ix_shadow_decisions_symbol", "shadow_decisions", ["symbol"])
    op.create_table("shadow_outcomes", sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("decision_id", sa.String(64), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("horizon_bar_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_close", sa.Float, nullable=False), sa.Column("horizon_close", sa.Float, nullable=False),
        sa.Column("realized_label", sa.String(12), nullable=False), sa.Column("hypothetical_pnl_usd", sa.Float),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_shadow_outcomes_decision_id", "shadow_outcomes", ["decision_id"], unique=True)


def downgrade() -> None:
    op.drop_table("shadow_outcomes")
    op.drop_table("shadow_decisions")
