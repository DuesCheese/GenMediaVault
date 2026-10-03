"""Shared tag groups and indexed membership for prompt organization."""
import sqlalchemy as sa
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('global_tag_groups',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('name_key', sa.Text(), nullable=False, unique=True),
        sa.Column('color', sa.String(7), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False))
    op.create_table('global_group_tags',
        sa.Column('group_id', sa.Uuid(), sa.ForeignKey('global_tag_groups.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('key', sa.Text(), primary_key=True),
        sa.Column('tag', sa.Text(), nullable=False))
    op.create_index('ix_global_group_tags_key', 'global_group_tags', ['key'])
    op.create_table('global_group_state', sa.Column('id', sa.Integer(), primary_key=True),
                    sa.Column('revision', sa.Integer(), nullable=False))
    op.execute('INSERT INTO global_group_state (id, revision) VALUES (1, 0)')


def downgrade():
    raise RuntimeError('全局分组含共享编辑数据；降级请恢复升级前备份')
