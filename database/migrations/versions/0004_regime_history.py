"""regime history

Revision ID: 0004_regime_history
Revises: 0003_experience_memory
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_regime_history"
down_revision = "0003_experience_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("regime_history", sa.Column("id", sa.Integer, primary_key=True), sa.Column("regime_id", sa.String(36), nullable=False, unique=True), sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False), sa.Column("symbol", sa.String(40), nullable=False), sa.Column("timeframe", sa.String(10), nullable=False), sa.Column("label", sa.String(30), nullable=False), sa.Column("confidence", sa.Float, nullable=False), sa.Column("measurements", sa.JSON, nullable=False), sa.Column("feature_version", sa.String(40), nullable=False), sa.Column("regime_model_version", sa.String(100), nullable=False), sa.Column("previous_label", sa.String(30)), sa.Column("transition", sa.Boolean, nullable=False), sa.Column("decision_id", sa.String(36)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_regime_history_symbol", "regime_history", ["symbol"])
    op.create_index("ix_regime_history_timeframe", "regime_history", ["timeframe"])
    op.create_index("ix_regime_history_label", "regime_history", ["label"])
    op.create_index("ix_regime_history_timestamp", "regime_history", ["timestamp"])


def downgrade() -> None:
    op.drop_table("regime_history")
