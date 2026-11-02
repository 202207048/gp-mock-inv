"""Persist shared stock and private portfolio briefings."""
from alembic import op
import sqlalchemy as sa

revision = 'j20261102_ai_brief_results'
down_revision = 'i20261028_sale_cost_basis'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ai_brief_results',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('symbol_code', sa.String(12), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.user_id'), nullable=True),
        sa.Column('account_id', sa.Integer(), sa.ForeignKey('account.account_id'), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_ai_brief_results_scope_created', 'ai_brief_results',
                    ['kind', 'symbol_code', 'user_id', 'account_id', 'created_at'])


def downgrade():
    op.drop_index('ix_ai_brief_results_scope_created', table_name='ai_brief_results')
    op.drop_table('ai_brief_results')
