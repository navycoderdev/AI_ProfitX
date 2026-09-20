"""model inference logs

Revision ID: 0005_model_inference_logs
Revises: 0004_regime_history
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_model_inference_logs"
down_revision = "0004_regime_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("model_inference_logs", sa.Column("id", sa.Integer, primary_key=True), sa.Column("inference_id", sa.String(36), nullable=False, unique=True), sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False), sa.Column("model_version", sa.String(100), nullable=False), sa.Column("feature_version", sa.String(40), nullable=False), sa.Column("feature_snapshot_id", sa.String(36), nullable=False), sa.Column("regime", sa.String(30), nullable=False), sa.Column("portfolio_context", sa.JSON, nullable=False), sa.Column("model_input", sa.JSON, nullable=False), sa.Column("model_output", sa.JSON, nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_model_inference_logs_timestamp", "model_inference_logs", ["timestamp"])
    op.create_index("ix_model_inference_logs_model_version", "model_inference_logs", ["model_version"])
    op.create_index("ix_model_inference_logs_feature_snapshot_id", "model_inference_logs", ["feature_snapshot_id"])


def downgrade() -> None:
    op.drop_table("model_inference_logs")
