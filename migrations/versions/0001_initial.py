"""Initial frozen schema. Runtime ORM changes cannot modify this migration."""
from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    source = Path(__file__).parents[1] / "initial_schema.sql"
    for statement in source.read_text(encoding="utf-8").split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade():
    raise RuntimeError("初始版本不提供破坏性降级；请恢复升级前备份")
