"""Store the actual average acquisition price at sale execution."""
from alembic import op
import sqlalchemy as sa

revision = 'i20261028_sale_cost_basis'
down_revision = 'h20261028_user_profile'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('orders', sa.Column('sale_cost_basis', sa.Numeric(20, 6), nullable=True))


def downgrade():
    op.drop_column('orders', 'sale_cost_basis')
