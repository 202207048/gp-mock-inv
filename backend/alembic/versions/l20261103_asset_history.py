"""Persist daily account valuations for weekly history."""
from alembic import op
import sqlalchemy as sa

revision = 'l20261103_asset_history'
down_revision = 'k20261102_ai_brief_reports'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('asset_history',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.Integer(), sa.ForeignKey('account.account_id', ondelete='CASCADE'), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('is_demo', sa.Boolean(), nullable=False, server_default=sa.false()),
        *(sa.Column(name, sa.Numeric(20, 2), nullable=False) for name in ['cash', 'market', 'debt', 'pending_proceeds', 'total']),
        sa.UniqueConstraint('account_id', 'date', name='uq_asset_history_account_date'))


def downgrade():
    op.drop_table('asset_history')
