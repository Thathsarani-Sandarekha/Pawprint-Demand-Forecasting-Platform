"""Persist local demonstration planner review decisions; no purchase order writes.

Revision ID: 0002_planner
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa

revision = '0002_planner'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'planner_decisions',
        sa.Column('decision_id', sa.String(36), primary_key=True),
        sa.Column('sku_id', sa.String(80), sa.ForeignKey('products.sku_id'), nullable=False),
        sa.Column('warehouse_id', sa.String(30), sa.ForeignKey('warehouses.warehouse_id'), nullable=False),
        sa.Column('as_of_date', sa.Date(), nullable=False),
        sa.Column('model_version', sa.String(80), nullable=False),
        sa.Column('review_period_days', sa.Integer(), nullable=False),
        sa.Column('snapshot_hash', sa.String(64), nullable=False),
        sa.Column('action', sa.String(12), nullable=False),
        sa.Column('recommended_units', sa.Integer(), nullable=False),
        sa.Column('approved_units', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(500), nullable=True),
        sa.Column('reviewer', sa.String(60), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("action IN ('approve','override')", name='ck_planner_action'),
        sa.CheckConstraint('recommended_units >= 0 AND approved_units >= 0', name='ck_planner_qty'),
        sa.CheckConstraint('review_period_days BETWEEN 1 AND 14', name='ck_planner_review_days'),
        sa.UniqueConstraint('sku_id', 'warehouse_id', 'as_of_date', 'model_version', 'review_period_days', name='uq_planner_snapshot'),
    )
    op.create_index('ix_planner_recent', 'planner_decisions', ['warehouse_id', 'created_at'])


def downgrade():
    op.drop_index('ix_planner_recent', table_name='planner_decisions')
    op.drop_table('planner_decisions')
