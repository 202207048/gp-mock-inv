"""Account-owned favorite libraries."""
from alembic import op
import sqlalchemy as sa

revision = 'm20261104_favorites'
down_revision = 'l20261103_asset_history'
branch_labels = None
depends_on = None


def upgrade():
    # Startup also creates this table on hosts without an Alembic release step.
    if not sa.inspect(op.get_bind()).has_table('favorite_libraries'):
        op.create_table('favorite_libraries',
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.user_id', ondelete='CASCADE'), primary_key=True),
            sa.Column('revision', sa.Integer(), nullable=False),
            sa.Column('codes', sa.JSON(), nullable=False),
            sa.Column('groups', sa.JSON(), nullable=False))


def downgrade():
    op.drop_table('favorite_libraries')
