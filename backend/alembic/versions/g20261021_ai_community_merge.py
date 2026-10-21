"""체결 시각과 커뮤니티 migration 이력을 하나의 head로 합친다."""

revision = "g20261021_ai_community_merge"
down_revision = (
    "f20261021_order_filled_at",
    "f20261021_community",
)
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
