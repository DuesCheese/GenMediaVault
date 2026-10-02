"""Local, exact-tag translations. Display translation never alters generation data."""
import re
import unicodedata
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, model_validator
from sqlalchemy import case, func, or_, select, text
from sqlalchemy.orm import Session

from .db import get_db
from .models import Audit, TagTranslation, User, now
from .schemas import Strict
from .security import admin, current_user
from .workspace import segments

router = APIRouter(prefix='/api/v1/translations', tags=['translations'])


def translation_key(value):
    value = re.sub(r'\\([()\[\]{},:])', r'\1', value)
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().replace('_', ' ').split())


class TranslationInput(Strict):
    tag: str = Field(min_length=1, max_length=500)
    translation: str = Field(default='', max_length=2000)
    groups: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(default_factory=list, max_length=32)

    @model_validator(mode='after')
    def clean(self):
        self.tag, self.translation = self.tag.strip(), self.translation.strip()
        self.groups = list(dict.fromkeys(g.strip() for g in self.groups if g.strip()))
        if not translation_key(self.tag) or any('\x00' in value for value in [self.tag, self.translation, *self.groups]):
            raise ValueError('Tag 不能为空，内容不能包含空字符')
        return self


class TranslationUpdate(TranslationInput):
    revision: int = Field(ge=1)


class TranslationOut(Strict):
    id: str
    tag: str
    translation: str
    groups: list[str]
    revision: int
    source: str


class TranslationPage(Strict):
    items: list[TranslationOut]
    total: int
    page: int
    limit: int


def output(item):
    return {field: getattr(item, field) for field in ('id', 'tag', 'translation', 'groups', 'revision', 'source')}


@router.get('', response_model=TranslationPage)
def browse(q: str = Query(default='', max_length=500), group: str = Query(default='', max_length=120),
           untranslated: bool = False, page: int = Query(default=1, ge=1, le=100000),
           limit: int = Query(default=50, ge=1, le=100),
           user: User = Depends(current_user), db: Session = Depends(get_db)):
    conditions = []
    key = translation_key(q)
    literal = TagTranslation.translation.contains(q.strip(), autoescape=True)
    if key:
        matches = [TagTranslation.key.contains(key, autoescape=True), literal]
        # Common short Chinese queries such as 白发 also find 白色头发, without inventing translations.
        if re.fullmatch(r'[\u3400-\u9fff]{2,12}', q.strip()):
            matches.append(TagTranslation.translation.like('%' + '%'.join(q.strip()) + '%'))
        conditions.append(or_(*matches))
    if group:
        conditions.append(TagTranslation.groups.contains([group]))
    if untranslated:
        conditions.append(TagTranslation.translation == '')
    count = db.scalar(select(func.count()).select_from(TagTranslation).where(*conditions))
    query = select(TagTranslation).where(*conditions).order_by(
        case((TagTranslation.key == key, 0), (TagTranslation.translation == q.strip(), 0),
             (literal, 1), else_=2), TagTranslation.key, TagTranslation.id)
    return {'items': [output(item) for item in db.scalars(query.offset((page - 1) * limit).limit(limit))],
            'total': count, 'page': page, 'limit': limit}


@router.get('/groups', response_model=list[str])
def groups(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return list(db.scalars(text('SELECT DISTINCT jsonb_array_elements_text(groups) AS name FROM tag_translations ORDER BY name')))


@router.post('', status_code=201, response_model=TranslationOut)
def create(body: TranslationInput, user: User = Depends(admin), db: Session = Depends(get_db)):
    item = TagTranslation(**body.model_dump(), key=translation_key(body.tag))
    db.add(item)
    db.flush()
    db.add(Audit(actor_id=user.id, action='translation.create', details={'id': item.id, 'tag': item.tag}))
    db.commit()
    return output(item)


@router.put('/{translation_id}', response_model=TranslationOut)
def edit(translation_id: UUID, body: TranslationUpdate, user: User = Depends(admin), db: Session = Depends(get_db)):
    item = db.scalar(select(TagTranslation).where(TagTranslation.id == str(translation_id)).with_for_update())
    if not item:
        raise HTTPException(404, '翻译不存在')
    if item.revision != body.revision:
        raise HTTPException(409, '翻译已被其他管理员修改，请刷新后重试')
    for field, value in body.model_dump(exclude={'revision'}).items():
        setattr(item, field, value)
    item.key, item.revision, item.updated_at, item.source = translation_key(item.tag), item.revision + 1, now(), 'manual'
    db.add(Audit(actor_id=user.id, action='translation.update', details={'id': item.id, 'tag': item.tag}))
    db.commit()
    return output(item)


@router.delete('/{translation_id}')
def remove(translation_id: UUID, revision: int = Query(ge=1), user: User = Depends(admin), db: Session = Depends(get_db)):
    item = db.scalar(select(TagTranslation).where(TagTranslation.id == str(translation_id)).with_for_update())
    if not item:
        raise HTTPException(404, '翻译不存在')
    if item.revision != revision:
        raise HTTPException(409, '翻译已被修改，请刷新后再删除')
    db.add(Audit(actor_id=user.id, action='translation.delete', details={'id': item.id, 'tag': item.tag}))
    db.delete(item)
    db.commit()
    return {'ok': True}


class LookupInput(Strict):
    texts: list[Annotated[str, Field(max_length=10000)]] = Field(max_length=1000)

    @model_validator(mode='after')
    def bounded(self):
        if sum(map(len, self.texts)) > 200000 or any('\x00' in value for value in self.texts):
            raise ValueError('查询内容过长或包含空字符')
        return self


class LookupItem(Strict):
    text: str
    translated: str
    matched: bool
    groups: list[str]


class LookupOut(Strict):
    items: list[LookupItem]


def parts(value):
    split = segments(value)
    if len(split) > 1:
        return '', split, ''
    for pattern in (r'([+-]?\d+(?:\.\d+)?::)(.*)(::)', r'(\()(.*)(:[+-]?\d+(?:\.\d+)?\))'):
        match = re.fullmatch(pattern, value, re.S)
        if match:
            return match[1], [match[2]], match[3]
    if len(value) > 2 and (value[0], value[-1]) in (('(', ')'), ('[', ']'), ('{', '}')):
        return value[0], [value[1:-1]], value[-1]
    return '', [], ''


def candidates(value, depth=0):
    yield translation_key(value)
    if depth < 16:
        for child in parts(value.strip())[1]:
            yield from candidates(child, depth + 1)


def translate(value, dictionary, depth=0):
    item = dictionary.get(translation_key(value))
    if item and item.translation:
        return item.translation, True, item.groups
    if depth < 16:
        opening, children, closing = parts(value.strip())
        if children:
            translated = [translate(child, dictionary, depth + 1) for child in children]
            if any(t[1] for t in translated):
                return opening + ', '.join(t[0] for t in translated) + closing, True, list(dict.fromkeys(g for t in translated for g in t[2]))
    return value, False, item.groups if item else []


@router.post('/lookup', response_model=LookupOut)
def lookup(body: LookupInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    keys = {key for value in body.texts for key in candidates(value)}
    if len(keys) > 10000:
        raise HTTPException(422, '一次查询最多 10000 个子词条')
    dictionary = {item.key: item for item in db.scalars(select(TagTranslation).where(TagTranslation.key.in_(keys)))}
    items = []
    for value in body.texts:
        translated, matched, groups = translate(value, dictionary)
        items.append({'text': value, 'translated': translated, 'matched': matched, 'groups': groups})
    return {'items': items}
