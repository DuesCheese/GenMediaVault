"""Shared searchable tag dictionary, seeded once from the user's reference data."""
import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    table = op.create_table('tag_translations',
        sa.Column('id', sa.Uuid(), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('tag', sa.Text(), nullable=False), sa.Column('key', sa.Text(), nullable=False, unique=True),
        sa.Column('translation', sa.Text(), nullable=False, server_default=''),
        sa.Column('groups', JSONB(), nullable=False, server_default='[]'),
        sa.Column('revision', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('source', sa.String(40), nullable=False, server_default='reference-v1'))
    source = Path(__file__).resolve().parents[2] / 'backend/genmedia/resources/tag-translations-v1.json'
    entries = json.loads(source.read_text(encoding='utf-8'))['entries']
    for offset in range(0, len(entries), 500):
        op.get_bind().execute(table.insert(), entries[offset:offset + 500])
    op.execute('CREATE INDEX ix_translation_key_trgm ON tag_translations USING gin (key gin_trgm_ops)')
    op.execute('CREATE INDEX ix_translation_text_trgm ON tag_translations USING gin (translation gin_trgm_ops)')
    op.execute('CREATE INDEX ix_translation_groups ON tag_translations USING gin (groups)')


def downgrade():
    raise RuntimeError('翻译表包含共享编辑数据；降级请恢复升级前备份')
