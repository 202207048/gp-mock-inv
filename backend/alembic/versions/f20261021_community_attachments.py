"""Community photos, polls and verified trade snapshots."""
from alembic import op
import sqlalchemy as sa

revision = 'f20261021_community'
down_revision = 'e20261007_misu'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('posts', sa.Column('attachments', sa.JSON(), nullable=True))
    op.create_table('post_votes',
        sa.Column('vote_id', sa.Integer(), primary_key=True),
        sa.Column('post_id', sa.Integer(), sa.ForeignKey('posts.post_id'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.user_id'), nullable=False),
        sa.Column('option_index', sa.Integer(), nullable=False),
        sa.UniqueConstraint('post_id', 'user_id', name='uq_post_vote_user'))


def downgrade():
    op.drop_table('post_votes')
    op.drop_column('posts', 'attachments')
