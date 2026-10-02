"""Shared prompt groups, reference attachments, tag suppression and character token scopes."""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE tags ADD COLUMN suppressed boolean NOT NULL DEFAULT false")
    op.execute("ALTER TABLE prompt_tokens ADD COLUMN scope varchar(80) NOT NULL DEFAULT 'base'")
    op.execute("""CREATE TABLE prompt_layouts (
        asset_id uuid PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
        revision integer NOT NULL, groups jsonb NOT NULL)""")
    op.execute("""CREATE TABLE attachments (
        id uuid PRIMARY KEY, created_at timestamptz NOT NULL,
        asset_id uuid NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        filename text NOT NULL, storage_key text NOT NULL, mime_type varchar(50) NOT NULL,
        sha256 varchar(64) NOT NULL, size bigint NOT NULL, caption text NOT NULL,
        character_index integer, created_by uuid NOT NULL REFERENCES users(id), deleted_at timestamptz)""")
    op.execute("CREATE INDEX ix_attachments_asset_id ON attachments(asset_id)")


def downgrade():
    raise RuntimeError("此迁移包含人工整理数据；降级请恢复升级前备份")
