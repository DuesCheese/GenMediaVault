"""Anonymous capability URLs: only explicitly listed read operations are exposed."""
import secrets
from datetime import timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import accessible, require_owner
from .config import settings
from .db import get_db
from .models import Asset, Attachment, ShareLink, User, now
from .schemas import Strict
from .security import current_user, token_hash

router = APIRouter(prefix='/api/v1')


class ShareInput(Strict):
    days: Literal[1, 7, 30] = 7


def share_out(link):
    return {'id': link.id, 'created_at': link.created_at, 'expires_at': link.expires_at,
            'revoked': link.revoked_at is not None, 'expired': link.expires_at <= now()}


@router.post('/assets/{asset_id}/shares', status_code=201)
def create_share(asset_id: UUID, body: ShareInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.execute(select(Asset.id).where(Asset.id == str(asset_id)).with_for_update())
    asset = accessible(db, user, asset_id)
    require_owner(user, asset)
    if asset.trashed_at:
        raise HTTPException(409, '回收站图片不能创建分享链接')
    token = secrets.token_urlsafe(32)
    link = ShareLink(asset_id=asset.id, token_hash=token_hash(token), created_by=user.id,
                     expires_at=now() + timedelta(days=body.days))
    db.add(link)
    db.commit()
    return {**share_out(link), 'path': f'/share/{token}'}


@router.get('/assets/{asset_id}/shares')
def shares(asset_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_owner(user, accessible(db, user, asset_id))
    return [share_out(link) for link in db.scalars(select(ShareLink).where(ShareLink.asset_id == str(asset_id))
                                                 .order_by(ShareLink.created_at.desc()).limit(100))]


@router.delete('/assets/{asset_id}/shares/{share_id}')
def revoke(asset_id: UUID, share_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_owner(user, accessible(db, user, asset_id))
    link = db.get(ShareLink, str(share_id))
    if not link or link.asset_id != str(asset_id):
        raise HTTPException(404, '分享链接不存在')
    link.revoked_at = now()
    db.commit()
    return {'ok': True}


def resolve_share(token: str, db: Session = Depends(get_db)):
    if len(token) != 43:
        raise HTTPException(404, '分享链接不存在或已失效')
    link = db.scalar(select(ShareLink).where(ShareLink.token_hash == token_hash(token)))
    asset = db.get(Asset, link.asset_id) if link else None
    creator = db.get(User, link.created_by) if link else None
    if not link or link.revoked_at or link.expires_at <= now() or not asset or asset.trashed_at or not creator or not creator.active:
        raise HTTPException(404, '分享链接不存在或已失效')
    return link, asset, creator


@router.get('/shared/{token}')
def shared(token: str, resolved=Depends(resolve_share), db: Session = Depends(get_db)):
    from .api import detail
    from .workspace import attachments, prompt_groups
    link, asset, creator = resolved
    value = detail(UUID(asset.id), creator, db)
    # Image parameters are shared, account-private notes/ratings and server paths are not.
    for field in ('notes', 'favorite', 'rating', 'review', 'files'):
        value.pop(field, None)
    value['raw'] = [{k: v for k, v in raw.items() if k != 'source'} for raw in value['raw']]
    for raw in value['raw']:
        if isinstance(raw['data'], dict):
            raw['data'] = {k: v for k, v in raw['data'].items() if k != 'origin'}
    value['conflicts'] = [{k: (v.replace('\\', '/').split('/')[-1] if k.endswith('_source') and isinstance(v, str) else v)
                           for k, v in conflict.items()} for conflict in value['conflicts']]
    value['original'] = f'/api/v1/shared/{token}/original'
    value['thumbnail'] = f'/api/v1/shared/{token}/thumbnail'
    value['prompt_groups'] = prompt_groups(UUID(asset.id), creator, db)
    value['attachments'] = [{**item, 'url': f'/api/v1/shared/{token}/attachments/{item["id"]}'}
                            for item in attachments(UUID(asset.id), creator, db)]
    value['expires_at'] = link.expires_at
    return value


@router.get('/shared/{token}/original')
def shared_original(token: str, download: bool = False, resolved=Depends(resolve_share), db: Session = Depends(get_db)):
    from .api import original
    _, asset, creator = resolved
    return original(UUID(asset.id), download, creator, db)


@router.get('/shared/{token}/thumbnail')
def shared_thumbnail(token: str, resolved=Depends(resolve_share)):
    _, asset, _ = resolved
    path = settings().data_dir / 'thumbnails' / f'{asset.id}.webp'
    if not path.is_file():
        raise HTTPException(404, '缩略图不存在')
    return FileResponse(path, media_type='image/webp')


@router.get('/shared/{token}/attachments/{attachment_id}')
def shared_attachment(token: str, attachment_id: UUID, resolved=Depends(resolve_share), db: Session = Depends(get_db)):
    from .workspace import attachment_path
    _, asset, _ = resolved
    item = db.get(Attachment, str(attachment_id))
    if not item or item.asset_id != asset.id or item.deleted_at:
        raise HTTPException(404, '附件不存在')
    path = attachment_path(item)
    if not path.is_file():
        raise HTTPException(404, '附件文件缺失')
    return FileResponse(path, media_type=item.mime_type)
