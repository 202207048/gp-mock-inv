"""Persist mock misu debts, receipts and idempotent simulated deposits."""
from alembic import op
import sqlalchemy as sa

revision = 'e20261007_misu'
down_revision = 'd20261006_automations'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('orders', sa.Column('funding_type', sa.String(10), nullable=False, server_default='현금'))
    op.create_table('misu_debts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.Integer(), sa.ForeignKey('account.account_id'), nullable=False),
        sa.Column('order_id', sa.Integer(), sa.ForeignKey('orders.order_id'), nullable=False, unique=True),
        sa.Column('request_id', sa.String(36), nullable=False),
        sa.Column('original', sa.Numeric(20, 2), nullable=False),
        sa.Column('remaining', sa.Numeric(20, 2), nullable=False),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('liquidate_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('frozen_until', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('account_id', 'request_id', name='uq_misu_request'))
    op.create_index('ix_misu_debts_account_id', 'misu_debts', ['account_id'])
    op.create_table('misu_settlements',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.Integer(), sa.ForeignKey('account.account_id'), nullable=False),
        sa.Column('order_id', sa.Integer(), sa.ForeignKey('orders.order_id'), nullable=False, unique=True),
        sa.Column('amount', sa.Numeric(20, 2), nullable=False),
        sa.Column('available_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('applied', sa.Boolean(), nullable=False))
    op.create_index('ix_misu_settlements_account_id', 'misu_settlements', ['account_id'])
    op.create_table('misu_payments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.Integer(), sa.ForeignKey('account.account_id'), nullable=False),
        sa.Column('request_id', sa.String(36), nullable=False),
        sa.Column('amount', sa.Numeric(20, 2), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('account_id', 'request_id', name='uq_misu_payment'))


def downgrade():
    for name in ('misu_payments', 'misu_settlements', 'misu_debts'):
        op.drop_table(name)
    op.drop_column('orders', 'funding_type')
