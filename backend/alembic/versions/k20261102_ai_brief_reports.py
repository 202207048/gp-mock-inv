"""Store user reports on AI briefings."""
from alembic import op
import sqlalchemy as sa

revision = 'k20261102_ai_brief_reports'
down_revision = 'j20261102_ai_brief_results'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ai_brief_reports',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.user_id'), nullable=False),
        sa.Column('account_id', sa.Integer(), sa.ForeignKey('account.account_id'), nullable=True),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('symbol_code', sa.String(12), nullable=False),
        sa.Column('brief_generated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('reason', sa.String(20), nullable=False),
        sa.Column('note', sa.String(200), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('user_id', 'kind', 'symbol_code', 'brief_generated_at', name='uq_ai_brief_reports_once'))
    op.create_index('ix_ai_brief_reports_user_created', 'ai_brief_reports', ['user_id', 'created_at'])


def downgrade():
    op.drop_index('ix_ai_brief_reports_user_created', table_name='ai_brief_reports')
    op.drop_table('ai_brief_reports')
