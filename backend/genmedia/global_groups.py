"""Global prompt groups: shared catalog, additive dictionary sync and live image projection."""
import copy
from uuid import UUID, NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, model_validator
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.orm import Session

from .db import get_db
from .models import Audit, GlobalGroupState, GlobalGroupTag, GlobalTagGroup, TagTranslation, User
from .schemas import Strict
from .security import admin, current_user
from .translations import parts, translation_key

router = APIRouter(prefix='/api/v1', tags=['global-groups'])
COLORS = ['#8fbcbb', '#b48ead', '#a3be8c', '#ebcb8b', '#bf616a', '#d3b888']


def catalog_revision(db):
    return db.scalar(select(GlobalGroupState.revision).where(GlobalGroupState.id == 1)) or 0


def lock_catalog(db):
    # Serialize small catalog mutations, including races between image publishing and dictionary sync.
    db.execute(text('SELECT pg_advisory_xact_lock(72411609)'))


def bump_catalog(db):
    state = db.get(GlobalGroupState, 1)
    if state:
        state.revision += 1
    else:
        db.add(GlobalGroupState(id=1, revision=1))
    db.flush()


def check_revision(group, revision):
    if group.revision != revision:
        raise HTTPException(409, '全局组已被修改，请刷新后重试')


def get_group(db, group_id):
    group = db.get(GlobalTagGroup, str(group_id))
    if not group:
        raise HTTPException(404, '全局分组不存在')
    return group


def group_output(group, count=0):
    return {'id': group.id, 'name': group.name, 'color': group.color, 'revision': group.revision, 'count': count}


def tag_keys(value, depth=0):
    """Match both exact names and leaves inside weights, while keeping displayed prompt bytes intact."""
    yield translation_key(value)
    if depth < 16:
        for child in parts(value.strip())[1]:
            yield from tag_keys(child, depth + 1)


def plain_tags(value, depth=0):
    children = parts(value.strip())[1] if depth < 16 else []
    if children:
        for child in children:
            yield from plain_tags(child, depth + 1)
    else:
        yield value.strip()


def token_members(tokens):
    return {translation_key(tag): tag for token in tokens for tag in plain_tags(token['text']) if translation_key(tag)}


def ensure_group(db, name, color):
    group = db.scalar(select(GlobalTagGroup).where(GlobalTagGroup.name_key == translation_key(name)))
    if not group:
        group = GlobalTagGroup(name=name.strip(), name_key=translation_key(name), color=color)
        db.add(group)
        db.flush()
    return group


def merge_members(db, group, members):
    if any(len(key.encode('utf-8')) > 2000 for key in members):
        raise HTTPException(422, '单个全局 Tag 过长（最多 2000 UTF-8 字节），请拆成独立词条')
    existing = set(db.scalars(select(GlobalGroupTag.key).where(GlobalGroupTag.group_id == group.id)))
    additions = [{'group_id': group.id, 'key': key, 'tag': tag} for key, tag in members.items() if key not in existing]
    if additions:
        db.execute(GlobalGroupTag.__table__.insert(), additions)
        group.revision += 1
    return len(additions)


class GroupInput(Strict):
    name: str = Field(min_length=1, max_length=120)
    color: str = Field(default='#8fbcbb', pattern=r'^#[0-9a-fA-F]{6}$')

    @model_validator(mode='after')
    def clean(self):
        self.name = self.name.strip()
        if not translation_key(self.name) or '\x00' in self.name:
            raise ValueError('组名不能为空或含空字符')
        return self


class GroupEdit(GroupInput):
    revision: int = Field(ge=1)


class MemberInput(Strict):
    tag: str = Field(min_length=1, max_length=10000)
    revision: int = Field(ge=1)
    previous_key: str | None = Field(default=None, max_length=10000)

    @model_validator(mode='after')
    def clean(self):
        self.tag = self.tag.strip()
        if not translation_key(self.tag) or '\x00' in self.tag or (self.previous_key and '\x00' in self.previous_key):
            raise ValueError('Tag 不能为空或含空字符')
        if len(translation_key(self.tag).encode('utf-8')) > 2000:
            raise ValueError('单个全局 Tag 最多 2000 UTF-8 字节')
        return self


class GroupOut(Strict):
    id: str
    name: str
    color: str
    revision: int
    count: int


class MemberOut(Strict):
    key: str
    tag: str
    translation: str


class MembersOut(Strict):
    group: GroupOut
    items: list[MemberOut]
    total: int
    page: int
    limit: int


@router.get('/tag-groups', response_model=list[GroupOut])
def browse_groups(q: str = Query(default='', max_length=500), user: User = Depends(current_user), db: Session = Depends(get_db)):
    condition = GlobalTagGroup.name_key.contains(translation_key(q), autoescape=True)
    member_match = select(GlobalGroupTag.group_id).outerjoin(TagTranslation, TagTranslation.key == GlobalGroupTag.key).where(
        or_(GlobalGroupTag.key.contains(translation_key(q), autoescape=True), TagTranslation.translation.contains(q.strip(), autoescape=True)))
    query = select(GlobalTagGroup, func.count(GlobalGroupTag.key)).outerjoin(GlobalGroupTag).where(
        or_(condition, GlobalTagGroup.id.in_(member_match))).group_by(GlobalTagGroup.id).order_by(GlobalTagGroup.name_key)
    return [group_output(group, count) for group, count in db.execute(query)]


@router.post('/tag-groups', status_code=201, response_model=GroupOut)
def create_group(body: GroupInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    lock_catalog(db)
    if db.scalar(select(GlobalTagGroup.id).where(GlobalTagGroup.name_key == translation_key(body.name))):
        raise HTTPException(409, '同名分组已存在，请在已有分组中添加 Tag')
    group = ensure_group(db, body.name, body.color)
    bump_catalog(db)
    db.add(Audit(actor_id=user.id, action='global_group.create', details={'id': group.id}))
    db.commit()
    return group_output(group)


@router.put('/tag-groups/{group_id}', response_model=GroupOut)
def edit_group(group_id: UUID, body: GroupEdit, user: User = Depends(current_user), db: Session = Depends(get_db)):
    lock_catalog(db)
    group = get_group(db, group_id)
    check_revision(group, body.revision)
    group.name, group.name_key, group.color = body.name, translation_key(body.name), body.color
    group.revision += 1
    bump_catalog(db)
    db.add(Audit(actor_id=user.id, action='global_group.edit', details={'id': group.id}))
    db.commit()
    return group_output(group, db.scalar(select(func.count()).select_from(GlobalGroupTag).where(GlobalGroupTag.group_id == group.id)))


@router.delete('/tag-groups/{group_id}')
def delete_group(group_id: UUID, revision: int = Query(ge=1), user: User = Depends(admin), db: Session = Depends(get_db)):
    lock_catalog(db)
    group = get_group(db, group_id)
    check_revision(group, revision)
    db.delete(group)
    bump_catalog(db)
    db.add(Audit(actor_id=user.id, action='global_group.delete', details={'id': str(group_id)}))
    db.commit()
    return {'ok': True}


@router.get('/tag-groups/{group_id}/tags', response_model=MembersOut)
def browse_members(group_id: UUID, q: str = Query(default='', max_length=500), page: int = Query(default=1, ge=1, le=100000),
                   limit: int = Query(default=50, ge=1, le=100), user: User = Depends(current_user), db: Session = Depends(get_db)):
    group = get_group(db, group_id)
    query = select(GlobalGroupTag, TagTranslation.translation).outerjoin(TagTranslation, TagTranslation.key == GlobalGroupTag.key).where(
        GlobalGroupTag.group_id == group.id, or_(GlobalGroupTag.key.contains(translation_key(q), autoescape=True),
                                                TagTranslation.translation.contains(q.strip(), autoescape=True)))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    count = db.scalar(select(func.count()).select_from(GlobalGroupTag).where(GlobalGroupTag.group_id == group.id))
    items = [{'key': tag.key, 'tag': tag.tag, 'translation': meaning or ''}
             for tag, meaning in db.execute(query.order_by(GlobalGroupTag.key).offset((page - 1) * limit).limit(limit))]
    return {'group': group_output(group, count), 'items': items, 'total': total, 'page': page, 'limit': limit}


@router.put('/tag-groups/{group_id}/tags')
def save_member(group_id: UUID, body: MemberInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    lock_catalog(db)
    group = get_group(db, group_id)
    check_revision(group, body.revision)
    key = translation_key(body.tag)
    existing = db.get(GlobalGroupTag, (group.id, key))
    if existing and key != body.previous_key:
        raise HTTPException(409, '此分组已包含相同 Tag')
    if body.previous_key is not None:
        previous = db.get(GlobalGroupTag, (group.id, body.previous_key))
        if not previous:
            raise HTTPException(404, 'Tag 已被删除，请刷新')
        if key == body.previous_key:
            previous.tag = body.tag
        else:
            db.delete(previous)
    if not existing:
        db.add(GlobalGroupTag(group_id=group.id, key=key, tag=body.tag))
    group.revision += 1
    bump_catalog(db)
    db.add(Audit(actor_id=user.id, action='global_group.tag.save', details={'id': group.id, 'key': key}))
    db.commit()
    return {'ok': True}


@router.delete('/tag-groups/{group_id}/tags')
def delete_member(group_id: UUID, key: str = Query(min_length=1, max_length=10000), revision: int = Query(ge=1),
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    lock_catalog(db)
    group = get_group(db, group_id)
    check_revision(group, revision)
    db.execute(delete(GlobalGroupTag).where(GlobalGroupTag.group_id == group.id, GlobalGroupTag.key == key))
    group.revision += 1
    bump_catalog(db)
    db.add(Audit(actor_id=user.id, action='global_group.tag.delete', details={'id': group.id, 'key': key}))
    db.commit()
    return {'ok': True}


@router.post('/translations/sync-tags')
def sync_translations(user: User = Depends(admin), db: Session = Depends(get_db)):
    lock_catalog(db)
    grouped = {}
    for entry in db.scalars(select(TagTranslation).order_by(TagTranslation.key)):
        for name in entry.groups or ['未分组']:
            normalized = translation_key(name)
            if normalized not in grouped:
                grouped[normalized] = (name, {})
            grouped[normalized][1][entry.key] = entry.tag
    added = 0
    for index, (name, members) in enumerate(grouped.values()):
        group = ensure_group(db, name, COLORS[index % len(COLORS)])
        added += merge_members(db, group, members)
    if grouped:
        bump_catalog(db)
    db.add(Audit(actor_id=user.id, action='translation.sync_tags', details={'groups': len(grouped), 'added': added}))
    db.commit()
    return {'groups': len(grouped), 'added': added}


def projected_groups(db, asset_id, stored, sources):
    manual = [copy.deepcopy(g) for g in stored if not g.get('global_group_id')]
    assigned = {t['key'] for g in manual for t in g['tokens']}
    keys = {key for source in sources for token in source['tokens'] for key in tag_keys(token['text'])}
    linked = {g['global_group_id'] for g in stored if g.get('global_group_id')}
    matching_ids = select(GlobalGroupTag.group_id).where(GlobalGroupTag.key.in_(keys))
    groups = list(db.scalars(select(GlobalTagGroup).where(or_(GlobalTagGroup.id.in_(matching_ids), GlobalTagGroup.id.in_(linked))).order_by(GlobalTagGroup.name_key)))
    memberships = {}
    for gid, key in db.execute(select(GlobalGroupTag.group_id, GlobalGroupTag.key).where(GlobalGroupTag.key.in_(keys))):
        memberships.setdefault(gid, set()).add(key)
    output = manual
    for group in groups:
        for source in sources:
            previous = next((g for g in stored if g.get('global_group_id') == group.id and g['source'] == source['id']), None)
            tokens = [t for t in source['tokens'] if t['key'] not in assigned and memberships.get(group.id, set()).intersection(tag_keys(t['text']))]
            if not tokens and not previous:
                continue
            output.append({'id': previous['id'] if previous else str(uuid5(NAMESPACE_URL, f'{asset_id}/{group.id}/{source["id"]}')),
                           'name': group.name, 'color': group.color, 'source': source['id'], 'tokens': tokens,
                           'global_group_id': group.id})
    positions = {g['id']: index for index, g in enumerate(stored)}
    return sorted(output, key=lambda g: positions.get(g['id'], len(stored)))


def sync_image_changes(db, previous, submitted, publish_id=None):
    """Apply only observed membership deltas, never replace a group with one image's subset."""
    before = {(g['global_group_id'], g['source']): g for g in previous if g.get('global_group_id')}
    after = {(g['global_group_id'], g['source']): g for g in submitted if g.get('global_group_id')}
    manual_overrides = {t['key'] for g in submitted if not g.get('global_group_id') for t in g['tokens']}
    deltas = {}
    for link in before.keys() | after.keys():
        old = {t['key']: t for t in before.get(link, {}).get('tokens', [])}
        new = {t['key']: t for t in after.get(link, {}).get('tokens', [])}
        removed, added = deltas.setdefault(link[0], (set(), {}))
        removed.update(key for token_id in old.keys() - new.keys() - manual_overrides for key in tag_keys(old[token_id]['text']))
        added.update(token_members([new[token_id] for token_id in new.keys() - old.keys()]))
    changed = False
    for gid, (removed, additions) in deltas.items():
        group = get_group(db, gid)
        if removed:
            # A displayed weighted phrase can match a member verbatim as well as individual leaves.
            db.execute(delete(GlobalGroupTag).where(GlobalGroupTag.group_id == gid, GlobalGroupTag.key.in_(removed)))
        added = merge_members(db, group, {key: tag for key, tag in additions.items() if key not in removed})
        if removed:
            group.revision += 1
        changed |= bool(removed or added)
    if publish_id:
        item = next((g for g in submitted if g['id'] == str(publish_id)), None)
        if not item or item.get('global_group_id'):
            raise HTTPException(422, '请选择尚未设为全局组的图片分组')
        group = ensure_group(db, item['name'], item['color'])
        merge_members(db, group, token_members(item['tokens']))
        item['global_group_id'] = group.id
        changed = True
    if changed:
        bump_catalog(db)
