from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint, Uuid)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now():
    return datetime.now(timezone.utc)


def uid():
    return str(uuid4())


class Identity:
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class User(Identity, Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default="user")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LoginSession(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Library(Identity, Base):
    __tablename__ = "libraries"
    name: Mapped[str] = mapped_column(String(200))
    mode: Mapped[str] = mapped_column(String(16))
    root_path: Mapped[str | None] = mapped_column(Text)
    watch_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Asset(Identity, Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("library_id", "sha256"),
        Index("ix_assets_imported", "imported_at", "id"),
        Index("ix_assets_library_imported", "library_id", "imported_at", "id"),
    )
    library_id: Mapped[str] = mapped_column(ForeignKey("libraries.id"), index=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    filename: Mapped[str] = mapped_column(Text)
    extension: Mapped[str] = mapped_column(String(10))
    mime_type: Mapped[str] = mapped_column(String(50))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    file_size: Mapped[int] = mapped_column(BigInteger)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    source_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="processing", index=True)
    warnings: Mapped[list] = mapped_column(JSONB, default=list)
    trashed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class PhysicalFile(Identity, Base):
    __tablename__ = "physical_files"
    __table_args__ = (UniqueConstraint("library_id", "path", "role"),)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    library_id: Mapped[str] = mapped_column(ForeignKey("libraries.id"))
    path: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), default="original")
    present: Mapped[bool] = mapped_column(Boolean, default=True)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    mtime_ns: Mapped[int] = mapped_column(BigInteger, default=0)
    sidecar_fingerprint: Mapped[str] = mapped_column(Text, default="")


class RawMetadata(Identity, Base):
    __tablename__ = "raw_metadata"
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(Text)
    parser: Mapped[str] = mapped_column(String(50))
    parser_version: Mapped[str] = mapped_column(String(20))
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    data: Mapped[dict] = mapped_column(JSONB)
    warnings: Mapped[list] = mapped_column(JSONB, default=list)


class Generation(Base):
    __tablename__ = "generations"
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    generator: Mapped[str] = mapped_column(String(80), default="unknown", index=True)
    model: Mapped[str | None] = mapped_column(Text, index=True)
    model_hash: Mapped[str | None] = mapped_column(Text)
    prompt: Mapped[str] = mapped_column(Text, default="")
    negative: Mapped[str] = mapped_column(Text, default="")
    seed: Mapped[str | None] = mapped_column(String(100), index=True)
    steps: Mapped[int | None] = mapped_column(Integer, index=True)
    cfg: Mapped[float | None] = mapped_column(Float, index=True)
    sampler: Mapped[str | None] = mapped_column(Text, index=True)
    scheduler: Mapped[str | None] = mapped_column(Text)
    normalized: Mapped[dict] = mapped_column(JSONB, default=dict)
    conflicts: Mapped[list] = mapped_column(JSONB, default=list)
    parsed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ModelRef(Identity, Base):
    __tablename__ = "model_refs"
    __table_args__ = (UniqueConstraint("kind", "name"),)
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(Text, index=True)


class AssetModel(Base):
    __tablename__ = "asset_models"
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    model_id: Mapped[str] = mapped_column(ForeignKey("model_refs.id"), primary_key=True)
    weight: Mapped[float | None] = mapped_column(Float)


class PromptToken(Identity, Base):
    __tablename__ = "prompt_tokens"
    __table_args__ = (Index("ix_prompt_token_lookup", "token", "polarity", "asset_id"),)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    token: Mapped[str] = mapped_column(Text)
    polarity: Mapped[str] = mapped_column(String(10))
    weight: Mapped[float] = mapped_column(Float, default=1)
    position: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(40), default="other")
    scope: Mapped[str] = mapped_column(String(80), default="base")


class Tag(Identity, Base):
    __tablename__ = "tags"
    name: Mapped[str] = mapped_column(Text, unique=True)
    namespace: Mapped[str] = mapped_column(Text, index=True)
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False)


class TagTranslation(Identity, Base):
    __tablename__ = "tag_translations"
    tag: Mapped[str] = mapped_column(Text)
    key: Mapped[str] = mapped_column(Text, unique=True)
    translation: Mapped[str] = mapped_column(Text, default="")
    groups: Mapped[list] = mapped_column(JSONB, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(40), default="manual")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class GlobalTagGroup(Identity, Base):
    __tablename__ = "global_tag_groups"
    name: Mapped[str] = mapped_column(Text)
    name_key: Mapped[str] = mapped_column(Text, unique=True)
    color: Mapped[str] = mapped_column(String(7), default="#8fbcbb")
    revision: Mapped[int] = mapped_column(Integer, default=1)


class GlobalGroupTag(Base):
    __tablename__ = "global_group_tags"
    group_id: Mapped[str] = mapped_column(ForeignKey("global_tag_groups.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True, index=True)
    tag: Mapped[str] = mapped_column(Text)


class GlobalGroupState(Base):
    __tablename__ = "global_group_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class PromptLayout(Base):
    __tablename__ = "prompt_layouts"
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    groups: Mapped[list] = mapped_column(JSONB, default=list)


class Attachment(Identity, Base):
    __tablename__ = "attachments"
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(Text)
    storage_key: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(String(50))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(BigInteger)
    caption: Mapped[str] = mapped_column(Text, default="")
    character_index: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssetTag(Base):
    __tablename__ = "asset_tags"
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[str] = mapped_column(ForeignKey("tags.id"), primary_key=True)
    source: Mapped[str] = mapped_column(String(100), primary_key=True)


class UserAsset(Base):
    __tablename__ = "user_assets"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    rating: Mapped[int | None] = mapped_column(Integer, index=True)
    review: Mapped[str] = mapped_column(String(20), default="unreviewed")
    notes: Mapped[str] = mapped_column(Text, default="")


class Collection(Identity, Base):
    __tablename__ = "collections"
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="static")
    query_ast: Mapped[dict | None] = mapped_column(JSONB)
    query: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)


class CollectionAsset(Base):
    __tablename__ = "collection_assets"
    collection_id: Mapped[str] = mapped_column(ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    source: Mapped[str] = mapped_column(String(100), primary_key=True, default="user")


class Rule(Identity, Base):
    __tablename__ = "rules"
    name: Mapped[str] = mapped_column(String(200))
    condition: Mapped[dict] = mapped_column(JSONB)
    actions: Mapped[list] = mapped_column(JSONB)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Job(Identity, Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", "available_at"),)
    kind: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    progress: Mapped[dict] = mapped_column(JSONB, default=dict)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(36))
    requested_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Audit(Identity, Base):
    __tablename__ = "audit_log"
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(80))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
