"""Persist user profile photos; existing user_name remains the nickname."""
from alembic import op
import sqlalchemy as sa

revision = 'h20261028_user_profile'
down_revision = 'g20261021_ai_community_merge'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('profile_image', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('users', 'profile_image')
