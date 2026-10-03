"""Shared prompt organization, protected reference images, exports and administrator backups."""
import hashlib
import os
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import Asset, AssetTag, Attachment, Audit, Generation, Job, PromptLayout, Tag, User, now
from .schemas import Strict
from .security import admin, current_user
from .services import enqueue, within

router = APIRouter(prefix="/api/v1")


def asset_exists(db, asset_id):
    asset = db.get(Asset, str(asset_id))
    if not asset:
        raise HTTPException(404, "图片不存在")
    return asset


def segments(text):
    """Split top-level commas without removing weights, escapes or grouping syntax."""
    result, start, depth, escaped, emphasis = [], 0, 0, False, False
    for i, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif char == ":" and text[i:i+2] == "::" and (i == 0 or text[i-1] != ":"):
            emphasis = not emphasis
        elif char == "," and depth == 0 and not emphasis:
            if text[start:i].strip():
                result.append(text[start:i].strip())
            start = i + 1
    if text[start:].strip():
        result.append(text[start:].strip())
    return result


def prompt_sources(normalized):
    sources = []
    scopes = [("base", "基础", normalized)]
    scopes += [(f"character:{i}", str(c.get("name") or f"角色 {i+1}"), c)
               for i, c in enumerate(normalized.get("characters", [])) if isinstance(c, dict)]
    for scope, label, values in scopes:
        for polarity, field, title in (("positive", "prompt", "正向"), ("negative", "negative", "负向")):
            source = f"{scope}:{polarity}"
            text = str(values.get(field) or "")
            sources.append({"id": source, "label": f"{label} · {title}", "text": text,
                            "tokens": [{"key": hashlib.sha256(f"{source}:{i}:{value}".encode()).hexdigest()[:24], "text": value}
                                       for i, value in enumerate(segments(text))]})
    return sources


class GroupToken(Strict):
    key: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=10000)


class PromptGroup(Strict):
    id: UUID
    name: str = Field(min_length=1, max_length=120)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    source: str = Field(min_length=1, max_length=80)
    tokens: list[GroupToken] = Field(default_factory=list, max_length=1000)
    global_group_id: UUID | None = None


class LayoutInput(Strict):
    revision: int = Field(ge=0)
    groups: list[PromptGroup] = Field(max_length=4096)
    catalog_revision: int = Field(default=0, ge=0)
    publish_group_id: UUID | None = None

    @model_validator(mode="after")
    def bounded(self):
        ids = [str(g.id) for g in self.groups]
        manual = [g for g in self.groups if not g.global_group_id]
        keys = [t.key for g in manual for t in g.tokens]
        links = [(g.global_group_id, g.source) for g in self.groups if g.global_group_id]
        if len(manual) > 32 or len(links) != len(set(links)):
            raise ValueError('最多 32 个人工分组，同一来源的全局组不能重复')
        if len(ids) != len(set(ids)) or len(keys) != len(set(keys)) or any(len(g.tokens) != len({t.key for t in g.tokens}) for g in self.groups):
            raise ValueError("分组 ID 和提示词不能重复分配")
        if sum(len(t.text) for g in self.groups for t in g.tokens) > 2000000:
            raise ValueError("分组内容超过长度限制")
        if any(not g.name.strip() or '\x00' in g.name or any('\x00' in t.text for t in g.tokens) for g in self.groups):
            raise ValueError('分组名称不能为空，内容不能含空字符')
        return self


@router.get("/assets/{asset_id}/prompt-groups")
def prompt_groups(asset_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from .global_groups import catalog_revision, projected_groups
    asset_exists(db, asset_id)
    layout, generation = db.get(PromptLayout, str(asset_id)), db.get(Generation, str(asset_id))
    revision = catalog_revision(db)
    sources = prompt_sources(generation.normalized if generation else {})
    return {"revision": layout.revision if layout else 0,
            "groups": projected_groups(db, str(asset_id), layout.groups if layout else [], sources),
            "catalog_revision": revision, "sources": sources}


@router.put("/assets/{asset_id}/prompt-groups")
def save_groups(asset_id: UUID, body: LayoutInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from .global_groups import catalog_revision, lock_catalog, projected_groups, sync_image_changes
    asset_exists(db, asset_id)
    # Lock the parent too, including first-save races where no layout row exists yet.
    db.execute(select(Asset.id).where(Asset.id == str(asset_id)).with_for_update())
    layout = db.get(PromptLayout, str(asset_id))
    if body.revision != (layout.revision if layout else 0):
        raise HTTPException(409, "其他成员已修改分组，请刷新后重新编辑")
    lock_catalog(db)
    groups = [group.model_dump(mode="json", exclude_none=True) for group in body.groups]
    generation = db.get(Generation, str(asset_id))
    sources = prompt_sources(generation.normalized if generation else {})
    previous = projected_groups(db, str(asset_id), layout.groups if layout else [], sources)
    if body.publish_group_id or any(g.get('global_group_id') for g in [*groups, *previous]):
        if body.catalog_revision != catalog_revision(db):
            raise HTTPException(409, '全局分组已更新，请刷新后重新编辑')
        sync_image_changes(db, previous, groups, body.publish_group_id)
        db.add(Audit(actor_id=user.id, action='global_group.image_sync', details={'asset_id': str(asset_id)}))
    if layout:
        layout.groups, layout.revision = groups, layout.revision + 1
    else:
        layout = PromptLayout(asset_id=str(asset_id), groups=groups, revision=1)
        db.add(layout)
    db.commit()
    return prompt_groups(asset_id, user, db)


@router.delete("/tags/{tag_id}")
def delete_tag(tag_id: UUID, user: User = Depends(admin), db: Session = Depends(get_db)):
    tag = db.scalar(select(Tag).where(Tag.id == str(tag_id)).with_for_update())
    if not tag:
        raise HTTPException(404, "标签不存在")
    db.execute(delete(AssetTag).where(AssetTag.tag_id == tag.id))
    tag.suppressed = True
    db.add(Audit(actor_id=user.id, action="tag.delete", details={"name": tag.name}))
    db.commit()
    return {"ok": True}


def attachment_path(attachment):
    root = settings().data_dir / "attachments"
    path = root / attachment.storage_key
    if not within(path, root):
        raise ValueError("附件路径无效")
    return path


def attachment_out(item):
    return {"id": item.id, "filename": item.filename, "caption": item.caption,
            "character_index": item.character_index, "size": item.size,
            "url": f"/api/v1/attachments/{item.id}/file", "created_at": item.created_at}


@router.get("/assets/{asset_id}/attachments")
def attachments(asset_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    asset_exists(db, asset_id)
    return [attachment_out(a) for a in db.scalars(select(Attachment).where(
        Attachment.asset_id == str(asset_id), Attachment.deleted_at.is_(None)).order_by(Attachment.created_at))]


@router.post("/assets/{asset_id}/attachments", status_code=201)
def upload_attachment(asset_id: UUID, file: UploadFile = File(...), caption: str = Form("", max_length=1000),
                      character_index: int | None = Form(None, ge=0, le=99),
                      user: User = Depends(current_user), db: Session = Depends(get_db)):
    asset_exists(db, asset_id)
    id = str(uuid4())
    root = settings().data_dir / "attachments"
    temporary = root / f"{id}.tmp"
    digest, size = hashlib.sha256(), 0
    try:
        with temporary.open("wb") as output:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > settings().upload_limit_mb * 1024 * 1024:
                    raise HTTPException(413, "参考图超过上传限制")
                digest.update(chunk)
                output.write(chunk)
        with Image.open(temporary) as image:
            if image.format not in ("PNG", "JPEG", "WEBP") or image.width * image.height > 64_000_000:
                raise ValueError("参考图只支持最多 6400 万像素的 PNG、JPEG、WebP")
            extension, mime = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"), "WEBP": ("webp", "image/webp")}[image.format]
            image.verify()
        key = f"{id}.{extension}"
        os.replace(temporary, root / key)
        item = Attachment(id=id, asset_id=str(asset_id), filename=(file.filename or key).replace("\\", "/").split("/")[-1],
                          storage_key=key, mime_type=mime, sha256=digest.hexdigest(), size=size,
                          caption=caption, character_index=character_index, created_by=user.id)
        db.add(item)
        db.commit()
        return attachment_out(item)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, "参考图不是有效图片") from exc
    finally:
        temporary.unlink(missing_ok=True)


@router.get("/attachments/{attachment_id}/file")
def get_attachment(attachment_id: UUID, download: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = db.get(Attachment, str(attachment_id))
    if not item or item.deleted_at:
        raise HTTPException(404, "参考图不存在")
    path = attachment_path(item)
    if not path.is_file():
        raise HTTPException(404, "参考图文件缺失")
    return FileResponse(path, media_type=item.mime_type, filename=item.filename if download else None)


@router.delete("/attachments/{attachment_id}")
def remove_attachment(attachment_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = db.get(Attachment, str(attachment_id))
    if not item:
        raise HTTPException(404, "参考图不存在")
    item.deleted_at = now()
    db.commit()
    return {"ok": True}


class ExportInput(Strict):
    asset_ids: list[UUID] = Field(min_length=1, max_length=1000)
    include_metadata: bool = False
    include_attachments: bool = True


@router.post("/exports", status_code=202)
def export(body: ExportInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ids = list(dict.fromkeys(str(id) for id in body.asset_ids))
    found = list(db.scalars(select(Asset.id).where(Asset.id.in_(ids), Asset.trashed_at.is_(None))))
    if len(found) != len(ids):
        raise HTTPException(404, "部分图片不存在或已在回收站")
    job = enqueue(db, "export", {**body.model_dump(exclude={"asset_ids"}), "asset_ids": ids}, user.id)
    db.commit()
    return {"job_id": job.id}


class BackupInput(Strict):
    label: str = Field(default="", max_length=120)
    include_indexed: bool = True


@router.post("/backups", status_code=202)
def create_backup(body: BackupInput, user: User = Depends(admin), db: Session = Depends(get_db)):
    job = enqueue(db, "backup", body.model_dump(), user.id)
    db.add(Audit(actor_id=user.id, action="backup.create", details={"job_id": job.id, "label": body.label}))
    db.commit()
    return {"job_id": job.id}


@router.get("/backups")
def backups(user: User = Depends(admin), db: Session = Depends(get_db)):
    return [{"id": j.id, "label": j.payload.get("label", ""), "created_at": j.created_at,
             "status": j.status, "error": j.error, "progress": j.progress,
             "result": {**j.result, "download": j.result.get("download") if
                        (settings().data_dir / "backups" / f"{j.id}.zip").is_file() else None}}
            for j in db.scalars(select(Job).where(Job.kind == "backup").order_by(Job.created_at.desc()).limit(100))]


@router.post("/maintenance/reparse-novelai", status_code=202)
def reparse_novelai(user: User = Depends(admin), db: Session = Depends(get_db)):
    job = enqueue(db, "reparse", {"generator": "novelai"}, user.id, deduplicate=True)
    db.commit()
    return {"job_id": job.id}


@router.get("/jobs/{job_id}")
def get_job(job_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = db.get(Job, str(job_id))
    if not job:
        raise HTTPException(404, "任务不存在")
    if (job.kind == "backup" and user.role != "admin") or (job.kind == "export" and job.requested_by != user.id):
        raise HTTPException(403, "没有此任务的访问权限")
    return {"id": job.id, "kind": job.kind, "status": job.status, "progress": job.progress,
            "result": job.result, "error": job.error, "created_at": job.created_at, "requested_by": job.requested_by}
