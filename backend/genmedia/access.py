"""A single visibility predicate for direct access, search and asynchronous exports."""
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy import or_, select, true
from sqlalchemy.orm import Session

from .db import get_db
from .models import Asset, Attachment
from .security import current_user


def visible(user):
    return true() if user.role == 'superadmin' else or_(Asset.is_public.is_(True), Asset.uploader_id == user.id)


def owns(user, asset):
    return user.role == 'superadmin' or asset.uploader_id == user.id


def require_owner(user, asset):
    if not owns(user, asset):
        raise HTTPException(403, '只有上传者或超级管理员可以管理公开状态和分享链接')


def accessible(db, user, asset_id):
    asset = db.scalar(select(Asset).where(Asset.id == str(asset_id), visible(user)))
    if not asset:
        raise HTTPException(404, '图片不存在或无权访问')
    return asset


def asset_guard(request: Request, db: Session = Depends(get_db)):
    """Protect all asset and attachment routes, including future additions."""
    asset_id = request.path_params.get('asset_id')
    attachment_id = request.path_params.get('attachment_id')
    if asset_id is None and attachment_id is None:
        return
    user = current_user(request, db)
    try:
        if attachment_id:
            item = db.get(Attachment, str(UUID(str(attachment_id))))
            if not item:
                raise HTTPException(404, '附件不存在')
            asset_id = item.asset_id
        accessible(db, user, str(UUID(str(asset_id))))
    except ValueError as exc:
        raise HTTPException(422, '图片 ID 无效') from exc


def export_access(db, job, user):
    ids = job.payload.get('asset_ids', [])
    count = len(list(db.scalars(select(Asset.id).where(Asset.id.in_(ids), visible(user), Asset.trashed_at.is_(None)))))
    if count != len(set(ids)):
        raise HTTPException(403, '导出包包含已取消公开、已转移或删除的图片，请重新导出')
