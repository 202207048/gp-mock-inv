"""주문 체결 시각을 보존한다. 과거 주문의 시각은 추정하지 않는다."""
from alembic import op
import sqlalchemy as sa

revision = "f20261021_order_filled_at"
down_revision = "e20261007_misu"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("orders", sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("orders", "filled_at")
