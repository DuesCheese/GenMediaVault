"""Uploader ownership, private spaces and revocable expiring shares."""
import secrets
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from argon2 import PasswordHasher

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    op.drop_constraint("ck_role", "users", type_="check")
    op.create_check_constraint("ck_role", "users", "role IN ('admin', 'superadmin', 'user')")
    owner = connection.scalar(sa.text("SELECT id FROM users WHERE username = 'admin'"))
    if owner is None:
        # Reserve admin without a known password on custom-username installations.
        owner = str(uuid4())
        connection.execute(sa.text("INSERT INTO users (id, created_at, username, password_hash, role, active) "
                           "VALUES (:id, now(), 'admin', :password, 'superadmin', false)"),
                           {'id': owner, 'password': PasswordHasher().hash(secrets.token_urlsafe(48))})
    op.execute("UPDATE users SET role = 'superadmin' WHERE username = 'admin'")
    op.add_column('assets', sa.Column('uploader_id', sa.Uuid(), sa.ForeignKey('users.id'), nullable=True))
    op.add_column('assets', sa.Column('is_public', sa.Boolean(), nullable=False, server_default=sa.true()))
    connection.execute(sa.text('UPDATE assets SET uploader_id = :owner'), {'owner': owner})
    op.alter_column('assets', 'uploader_id', nullable=False)
    op.alter_column('assets', 'is_public', server_default=sa.false())
    for constraint in sa.inspect(connection).get_unique_constraints('assets'):
        if set(constraint['column_names']) == {'library_id', 'sha256'}:
            op.drop_constraint(constraint['name'], 'assets', type_='unique')
    op.create_unique_constraint('uq_asset_owner_hash', 'assets', ['library_id', 'uploader_id', 'sha256'])
    op.create_index('ix_assets_uploader_id', 'assets', ['uploader_id'])
    op.create_index('ix_assets_is_public', 'assets', ['is_public'])
    op.create_table('share_links',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('asset_id', sa.Uuid(), sa.ForeignKey('assets.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token_hash', sa.String(64), unique=True, nullable=False),
        sa.Column('created_by', sa.Uuid(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True)))
    op.create_index('ix_share_links_asset_id', 'share_links', ['asset_id'])


def downgrade():
    raise RuntimeError('私人空间不能安全降级；请恢复升级前备份')
