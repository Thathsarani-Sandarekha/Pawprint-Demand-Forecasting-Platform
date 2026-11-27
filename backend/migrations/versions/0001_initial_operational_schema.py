"""Initial versioned pet retail tables: operational data only.

Revision ID: 0001_initial
Revises:
"""
from alembic import op
from app.db.models import metadata

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

# Explicit, dependency-aware ordering; do not auto-import evaluation_only files.
CREATION_ORDER = (
    "suppliers", "warehouses", "products", "promotions", "daily_sales",
    "purchase_orders", "inventory_snapshot", "stock_lots", "dataset_imports",
)


def upgrade() -> None:
    # Explicit table allowlist; tables are defined once in app.db.models.
    bind = op.get_bind()
    for name in CREATION_ORDER:
        metadata.tables[name].create(bind=bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(CREATION_ORDER):
        metadata.tables[name].drop(bind=bind, checkfirst=False)
