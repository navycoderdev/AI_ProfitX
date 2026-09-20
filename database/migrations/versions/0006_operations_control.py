"""operations control center

Revision ID: 0006_operations_control
Revises: 0005_model_inference_logs
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_operations_control"
down_revision = "0005_model_inference_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("system_control_state", sa.Column("key", sa.String(80), primary_key=True), sa.Column("value", sa.JSON, nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("operational_alerts", sa.Column("id", sa.Integer, primary_key=True), sa.Column("alert_id", sa.String(36), nullable=False, unique=True), sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False), sa.Column("severity", sa.String(20), nullable=False), sa.Column("code", sa.String(60), nullable=False), sa.Column("message", sa.Text, nullable=False), sa.Column("details", sa.JSON, nullable=False), sa.Column("acknowledged_at", sa.DateTime(timezone=True)), sa.Column("acknowledged_by", sa.String(100)))
    op.create_index("ix_operational_alerts_timestamp", "operational_alerts", ["timestamp"])
    op.create_index("ix_operational_alerts_severity", "operational_alerts", ["severity"])
    op.create_index("ix_operational_alerts_code", "operational_alerts", ["code"])


def downgrade() -> None:
    op.drop_table("operational_alerts"); op.drop_table("system_control_state")
