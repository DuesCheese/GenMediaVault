import base64
import hashlib
import json
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import schemas as s
from .config import settings
from .db import get_db
from .models import (Asset, AssetTag, Audit, Collection, CollectionAsset, Generation, Job, Library,
                     LoginSession, PhysicalFile, PromptToken, RawMetadata, Rule, Tag, User, UserAsset, now)
from .parsers import EXTENSIONS, PARSER_VERSION, canonical
from .search import FIELDS, compile_ast, parse, to_dsl, validate
from .security import (admin, create_session, current_user, hash_password, token_hash, verify_password)
from .services import add_collection, add_tag, allowed_import, checked_original, enqueue

router = APIRouter(prefix="/api/v1")
attempts: dict[str, deque] = defaultdict(deque)


def find(db, model, id):
    obj = db.get(model, str(id))
    if obj is None:
        raise HTTPException(404, "记录不存在")
    return obj


def audit(db, user, action, details=None):
    db.add(Audit(actor_id=user.id, action=action, details=details or {}))


def user_out(user):
    return {"id": user.id, "username": user.username, "role": user.role, "active": user.active}


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok", "version": "0.3.0"}


@router.post("/auth/login", response_model=s.SessionOut)
def login(body: s.LoginInput, request: Request, response: Response, db: Session = Depends(get_db)):
    address = request.client.host if request.client else "unknown"
    key = address + ":" + body.username.lower()
    bucket = attempts[key]
    current = time.monotonic()
    while bucket and current - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= 10:
        raise HTTPException(429, "登录尝试过于频繁，请稍后再试")
    bucket.append(current)
    user = db.scalar(select(User).where(User.username == body.username))
    if not user or not user.active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    previous = request.cookies.get("gmv_session")
    if previous:
        db.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(previous)))
    token, csrf = create_session(db, user, settings().session_hours)
    response.set_cookie("gmv_session", token, httponly=True, secure=settings().secure_cookie,
                        samesite="lax", max_age=settings().session_hours * 3600, path="/")
    attempts.pop(key, None)
    return {"user": user_out(user), "csrf": csrf}


@router.get("/auth/me", response_model=s.SessionOut)
def me(request: Request, user: User = Depends(current_user)):
    return {"user": user_out(user), "csrf": request.state.login_session.csrf}


@router.post("/auth/logout")
def logout(request: Request, response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(request.state.login_session)
    db.commit()
    response.delete_cookie("gmv_session", path="/")
    return {"ok": True}


@router.post("/auth/password")
def password(body: s.PasswordInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(400, "原密码不正确")
    user.password_hash = hash_password(body.password)
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    db.commit()
    return {"ok": True, "message": "密码已修改，请重新登录"}


@router.get("/users", response_model=list[s.UserOut])
def users(user: User = Depends(admin), db: Session = Depends(get_db)):
    return [user_out(u) for u in db.scalars(select(User).order_by(User.created_at))]


@router.post("/users", response_model=s.UserOut, status_code=201)
def create_user(body: s.UserInput, user: User = Depends(admin), db: Session = Depends(get_db)):
    if db.scalar(select(User.id).where(User.username == body.username)):
        raise HTTPException(409, "用户名已存在")
    added = User(username=body.username, password_hash=hash_password(body.password), role=body.role)
    db.add(added)
    audit(db, user, "user.create", {"username": body.username})
    db.commit()
    return user_out(added)


@router.patch("/users/{user_id}", response_model=s.UserOut)
def update_user(user_id: UUID, body: s.UserUpdate, user: User = Depends(admin), db: Session = Depends(get_db)):
    target = find(db, User, user_id)
    if target.id == user.id and (body.active is False or body.role == "user"):
        raise HTTPException(400, "不能停用或降级当前管理员自身")
    if body.password:
        target.password_hash = hash_password(body.password)
    if body.active is not None:
        target.active = body.active
    if body.role is not None:
        target.role = body.role
    db.execute(delete(LoginSession).where(LoginSession.user_id == target.id))
    audit(db, user, "user.update", {"id": target.id})
    db.commit()
    return user_out(target)


@router.get("/libraries")
def libraries(user: User = Depends(current_user), db: Session = Depends(get_db)):
    counts = dict(db.execute(select(Asset.library_id, func.count()).where(Asset.trashed_at.is_(None))
                            .group_by(Asset.library_id)).all())
    return [{"id": lib.id, "name": lib.name, "mode": lib.mode, "root_path": lib.root_path if user.role == "admin" else None,
             "watch_enabled": lib.watch_enabled, "count": counts.get(lib.id, 0), "last_scan_at": lib.last_scan_at}
            for lib in db.scalars(select(Library).order_by(Library.created_at))]


@router.post("/libraries", status_code=201)
def create_library(body: s.LibraryInput, user: User = Depends(admin), db: Session = Depends(get_db)):
    root = str(allowed_import(body.root_path or "")) if body.mode == "indexed" else None
    library = Library(name=body.name, mode=body.mode, root_path=root,
                      watch_enabled=body.watch_enabled if body.mode == "indexed" else False)
    db.add(library)
    db.flush()
    audit(db, user, "library.create", {"id": library.id})
    if body.mode == "indexed":
        enqueue(db, "scan", {"library_id": library.id}, user.id)
    db.commit()
    return {"id": library.id}


@router.patch("/libraries/{library_id}")
def update_library(library_id: UUID, body: s.LibraryUpdate, user: User = Depends(admin), db: Session = Depends(get_db)):
    library = find(db, Library, library_id)
    library.name, library.watch_enabled = body.name, body.watch_enabled if library.mode == "indexed" else False
    db.commit()
    return {"ok": True}


@router.post("/libraries/{library_id}/scan", status_code=202)
def scan(library_id: UUID, user: User = Depends(admin), db: Session = Depends(get_db)):
    library = find(db, Library, library_id)
    if library.mode != "indexed":
        raise HTTPException(400, "托管库通过上传导入")
    job = enqueue(db, "scan", {"library_id": library.id}, user.id, deduplicate=True)
    db.commit()
    return {"job_id": job.id}


@router.post("/imports", status_code=202)
async def upload(library_id: UUID = Form(...), files: list[UploadFile] = File(...),
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    library = find(db, Library, library_id)
    if library.mode != "managed":
        raise HTTPException(400, "只能上传到托管库")
    if not files or len(files) > 200:
        raise HTTPException(400, "每批最多上传 200 个文件，可分批导入")
    directory = settings().data_dir / "staging" / str(uuid4())
    directory.mkdir(parents=True)
    saved, names = [], set()
    try:
        for file in files:
            name = (file.filename or "").replace("\\", "/").split("/")[-1]
            if not name or len(name) > 240 or name in (".", "..") or any(c in name for c in '<>:"|?*\x00') or Path(name).is_reserved() or name.casefold() in names:
                raise HTTPException(400, "文件名为空、重复或不合法，请分批上传同名文件")
            names.add(name.casefold())
            suffix = Path(name).suffix.lower()
            if suffix not in EXTENSIONS | {".json", ".txt"}:
                raise HTTPException(400, f"不支持的格式：{suffix}")
            limit = (settings().upload_limit_mb if suffix in EXTENSIONS else settings().metadata_limit_mb) * 1024**2
            total = 0
            target = directory / name
            with target.open("wb") as stream:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > limit:
                        raise HTTPException(413, f"文件超过大小限制：{name}")
                    stream.write(chunk)
            if suffix in EXTENSIONS:
                saved.append({"name": name, "path": str(target.resolve())})
        if not saved:
            raise HTTPException(400, "请将 Sidecar 和对应图片一起上传")
        job = enqueue(db, "import", {"library_id": library.id, "files": saved}, user.id)
        db.commit()
        return {"job_id": job.id}
    except Exception:
        # Only files in this request's freshly-created staging directory are removed.
        for created in directory.iterdir():
            if created.is_file():
                created.unlink()
        directory.rmdir()
        raise
    finally:
        for file in files:
            await file.close()


def asset_rows(db, assets, user_id):
    ids = [asset.id for asset in assets]
    if not ids:
        return []
    generations = {g.asset_id: g for g in db.scalars(select(Generation).where(Generation.asset_id.in_(ids)))}
    personal = {p.asset_id: p for p in db.scalars(select(UserAsset).where(UserAsset.user_id == user_id,
                                                                       UserAsset.asset_id.in_(ids)))}
    tags = defaultdict(set)
    for asset_id, name in db.execute(select(AssetTag.asset_id, Tag.name).join(Tag).where(AssetTag.asset_id.in_(ids))):
        tags[asset_id].add(name)
    return [{"id": a.id, "library_id": a.library_id, "filename": a.filename, "width": a.width,
             "height": a.height, "file_size": a.file_size, "imported_at": a.imported_at.isoformat(),
             "generator": generations[a.id].generator if a.id in generations else "unknown",
             "model": generations[a.id].model if a.id in generations else None,
             "prompt": generations[a.id].prompt[:240] if a.id in generations else "",
             "favorite": personal[a.id].favorite if a.id in personal else False,
             "rating": personal[a.id].rating if a.id in personal else None,
             "review": personal[a.id].review if a.id in personal else "unreviewed",
             "tags": sorted(tags[a.id]), "thumbnail": f"/api/v1/assets/{a.id}/thumbnail",
             "warnings": a.warnings, "trashed": a.trashed_at is not None} for a in assets]


@router.post("/search/parse")
def parse_search(body: s.ParseInput, user: User = Depends(current_user)):
    ast = validate(body.ast) if body.ast is not None else parse(body.query)
    return {"ast": ast, "query": to_dsl(ast), "warnings": [], "errors": []}


@router.get("/search/fields")
def search_fields(user: User = Depends(current_user)):
    return FIELDS


@router.get("/search/suggest")
def suggest(field: str = "model", q: str = Query(default="", max_length=200),
            user: User = Depends(current_user), db: Session = Depends(get_db)):
    columns = {"model": Generation.model, "generator": Generation.generator, "sampler": Generation.sampler,
               "tag": Tag.name}
    if field not in columns:
        return [name for name in FIELDS if name.startswith(q)][:20]
    column = columns[field]
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return list(db.scalars(select(column).where(column.is_not(None), column.ilike(escaped + "%", escape="\\"))
                           .distinct().order_by(column).limit(20)))


@router.post("/search", response_model=s.SearchOut)
def search(body: s.SearchInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ast = validate(body.ast) if body.ast is not None else parse(body.query)
    filters = [Asset.trashed_at.is_not(None) if body.trash else Asset.trashed_at.is_(None), compile_ast(ast, user.id)]
    if body.library_id:
        filters.append(Asset.library_id == str(body.library_id))
    if body.collection_id:
        collection = find(db, Collection, body.collection_id)
        if collection.kind == "smart":
            filters.append(compile_ast(collection.query_ast, user.id))
        else:
            filters.append(Asset.id.in_(select(CollectionAsset.asset_id).where(CollectionAsset.collection_id == collection.id)))
    sort_columns = {"imported_desc": Asset.imported_at, "imported_asc": Asset.imported_at,
                    "created_desc": func.coalesce(Asset.source_created_at, Asset.imported_at),
                    "size_desc": Asset.file_size, "resolution_desc": Asset.width * Asset.height,
                    "filename_asc": Asset.filename,
                    "rating_desc": func.coalesce(select(UserAsset.rating).where(UserAsset.asset_id == Asset.id,
                                                   UserAsset.user_id == user.id).scalar_subquery(), 0)}
    column = sort_columns[body.sort]
    descending = body.sort.endswith("desc")
    signature = hashlib.sha256(json.dumps({"ast": ast, "library": str(body.library_id),
                 "collection": str(body.collection_id), "trash": body.trash, "sort": body.sort,
                 "user": user.id}, sort_keys=True).encode()).hexdigest()[:20]
    page_filters = list(filters)
    if body.cursor:
        try:
            cursor = json.loads(base64.urlsafe_b64decode(body.cursor))
            if cursor["signature"] != signature:
                raise ValueError()
            value = cursor["value"]
            if body.sort in ("imported_desc", "imported_asc", "created_desc"):
                value = datetime.fromisoformat(value)
            elif body.sort != "filename_asc":
                value = int(value)
            id_value = str(UUID(cursor["id"]))
            boundary = tuple_(column, Asset.id)
            page_filters.append(boundary < (value, id_value) if descending else boundary > (value, id_value))
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(400, "分页游标无效，请重新搜索") from exc
    query = select(Asset, column.label("sort_value")).where(*page_filters).order_by(
        column.desc() if descending else column.asc(), Asset.id.desc() if descending else Asset.id.asc()).limit(body.limit + 1)
    rows = list(db.execute(query))
    more = len(rows) > body.limit
    rows = rows[:body.limit]
    cursor = None
    if more and rows:
        asset, value = rows[-1]
        cursor = base64.urlsafe_b64encode(json.dumps({"signature": signature, "id": asset.id,
                                                    "value": value.isoformat() if isinstance(value, datetime) else value}).encode()).decode()
    facets = None
    if body.facets:
        facets = {"total": db.scalar(select(func.count()).select_from(Asset).where(*filters)),
                  "generators": dict(db.execute(select(Generation.generator, func.count()).join(Asset)
                      .where(*filters).group_by(Generation.generator)).all())}
    return {"items": asset_rows(db, [r[0] for r in rows], user.id), "next_cursor": cursor, "ast": ast, "facets": facets}


@router.get("/assets/{asset_id}")
def detail(asset_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    asset = find(db, Asset, asset_id)
    generation = db.get(Generation, asset.id)
    personal = db.get(UserAsset, (user.id, asset.id))
    result = asset_rows(db, [asset], user.id)[0]
    result.update({"generation": generation.normalized if generation else {}, "notes": personal.notes if personal else "",
                   "conflicts": generation.conflicts if generation else [], "sha256": asset.sha256,
                   "original": f"/api/v1/assets/{asset.id}/original",
                   "raw": [{"id": r.id, "source": r.source, "parser": r.parser, "parser_version": r.parser_version,
                            "created_at": r.created_at, "data": r.data, "warnings": r.warnings}
                           for r in db.scalars(select(RawMetadata).where(RawMetadata.asset_id == asset.id)
                                                .order_by(RawMetadata.created_at.desc()).limit(20))],
                   "tag_sources": [{"name": name, "source": source} for name, source in db.execute(select(Tag.name, AssetTag.source).join(AssetTag).where(AssetTag.asset_id == asset.id))],
                   "tokens": [{"token": t.token, "weight": t.weight, "polarity": t.polarity, "scope": t.scope,
                               "category": t.category} for t in db.scalars(select(PromptToken)
                               .where(PromptToken.asset_id == asset.id).order_by(PromptToken.polarity, PromptToken.position))],
                   "files": [{"path": f.path, "present": f.present} for f in db.scalars(select(PhysicalFile)
                               .where(PhysicalFile.asset_id == asset.id))]})
    return result


@router.get("/assets/{asset_id}/original")
def original(asset_id: UUID, download: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    asset = find(db, Asset, asset_id)
    library = db.get(Library, asset.library_id)
    for file in db.scalars(select(PhysicalFile).where(PhysicalFile.asset_id == asset.id,
                                                     PhysicalFile.role == "original", PhysicalFile.present.is_(True))):
        path = checked_original(file, library)
        if path.is_file():
            return FileResponse(path, media_type=asset.mime_type, filename=asset.filename if download else None,
                                headers={"Cache-Control": "private, no-store"})
    raise HTTPException(404, "原文件已缺失，请检查媒体库挂载")


@router.get("/assets/{asset_id}/thumbnail")
def get_thumbnail(asset_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    find(db, Asset, asset_id)
    path = settings().data_dir / "thumbnails" / f"{asset_id}.webp"
    if not path.is_file():
        raise HTTPException(404, "缩略图尚未生成")
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "private, no-store"})


@router.get("/assets/{asset_id}/workflow")
def workflow(asset_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    find(db, Asset, asset_id)
    generation = db.get(Generation, str(asset_id))
    value = generation.normalized.get("workflow") if generation else None
    if not value:
        raise HTTPException(404, "没有可下载的界面工作流")
    return Response(json.dumps(value, ensure_ascii=False), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{asset_id}.workflow.json"'})


@router.patch("/assets/{asset_id}/personal")
def update_personal(asset_id: UUID, body: s.PersonalInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    find(db, Asset, asset_id)
    values = body.model_dump(exclude_unset=True)
    for key, value in values.items():
        if key != "rating" and value is None:
            raise HTTPException(422, f"{key} 不能为 null")
    if values:
        db.execute(insert(UserAsset).values(user_id=user.id, asset_id=str(asset_id), **values)
                   .on_conflict_do_update(index_elements=[UserAsset.user_id, UserAsset.asset_id], set_=values))
        db.commit()
    return {"ok": True}


@router.post("/assets/bulk", status_code=202)
def bulk(body: s.BulkInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ids = list(dict.fromkeys(str(id) for id in body.asset_ids))
    action, value = body.action, body.value
    if action in ("trash", "restore", "reparse") and user.role != "admin":
        raise HTTPException(403, "此操作需要管理员权限")
    assets = list(db.scalars(select(Asset).where(Asset.id.in_(ids))))
    if len(assets) != len(ids):
        raise HTTPException(404, "部分资产已不存在")
    if action in ("export", "reparse"):
        job = enqueue(db, action, {"asset_ids": ids}, user.id)
        db.commit()
        return {"job_id": job.id}
    if action in ("add_tag", "remove_tag", "collection_add", "collection_remove") and not isinstance(value, str):
        raise HTTPException(422, "此操作需要文本值")
    if action == "rating" and (isinstance(value, bool) or value not in (None, 1, 2, 3, 4, 5)):
        raise HTTPException(422, "评分必须是 1–5 或 null")
    if action == "favorite" and not isinstance(value, bool):
        raise HTTPException(422, "收藏需要布尔值")
    if action == "review" and value not in ("unreviewed", "keep", "maybe", "reject"):
        raise HTTPException(422, "筛选状态无效")
    if action.startswith("collection_"):
        try:
            value = str(UUID(value))
        except ValueError as exc:
            raise HTTPException(422, "集合 ID 无效") from exc
    for asset in assets:
        if action == "add_tag":
            add_tag(db, asset.id, value)
        elif action == "remove_tag":
            tag_id = db.scalar(select(Tag.id).where(Tag.name == canonical(value)))
            db.execute(delete(AssetTag).where(AssetTag.asset_id == asset.id, AssetTag.tag_id == tag_id,
                                              AssetTag.source == "user"))
        elif action == "collection_add":
            add_collection(db, asset.id, value)
        elif action == "collection_remove":
            db.execute(delete(CollectionAsset).where(CollectionAsset.asset_id == asset.id,
                        CollectionAsset.collection_id == value, CollectionAsset.source == "user"))
        elif action in ("favorite", "rating", "review"):
            db.execute(insert(UserAsset).values(user_id=user.id, asset_id=asset.id, **{action: value})
                       .on_conflict_do_update(index_elements=[UserAsset.user_id, UserAsset.asset_id], set_={action: value}))
        elif action == "trash":
            asset.trashed_at = now()
        elif action == "restore":
            asset.trashed_at = None
    audit(db, user, f"asset.{action}", {"asset_ids": ids})
    db.commit()
    return {"updated": len(ids)}


@router.get("/tags")
def tags(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{"id": id, "name": name, "namespace": namespace, "count": count}
            for id, name, namespace, count in db.execute(select(Tag.id, Tag.name, Tag.namespace,
             func.count(func.distinct(AssetTag.asset_id))).outerjoin(AssetTag)
             .where(Tag.suppressed.is_(False)).group_by(Tag.id).order_by(Tag.name).limit(2000))]


def collection_out(item):
    return {"id": item.id, "name": item.name, "kind": item.kind, "query": item.query,
            "ast": item.query_ast, "revision": item.revision}


@router.get("/collections")
def collections(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [collection_out(c) for c in db.scalars(select(Collection).order_by(Collection.name))]


@router.post("/collections", status_code=201)
def create_collection(body: s.CollectionInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ast = (validate(body.ast) if body.ast is not None else parse(body.query)) if body.kind == "smart" else None
    collection = Collection(name=body.name, kind=body.kind, query_ast=ast, query=to_dsl(ast) if ast else "")
    db.add(collection)
    db.commit()
    return collection_out(collection)


@router.patch("/collections/{collection_id}")
def edit_collection(collection_id: UUID, body: s.CollectionInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    collection = db.scalar(select(Collection).where(Collection.id == str(collection_id)).with_for_update())
    if not collection:
        raise HTTPException(404, "集合不存在")
    if collection.revision != body.revision:
        raise HTTPException(409, "集合已被其他用户修改，请刷新")
    if body.kind != collection.kind:
        raise HTTPException(400, "集合类型不能变更，请创建新集合")
    collection.name = body.name
    if body.kind == "smart":
        collection.query_ast = validate(body.ast) if body.ast is not None else parse(body.query)
        collection.query = to_dsl(collection.query_ast)
    collection.revision += 1
    db.commit()
    return collection_out(collection)


@router.delete("/collections/{collection_id}")
def remove_collection(collection_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    collection = find(db, Collection, collection_id)
    for rule in db.scalars(select(Rule)):
        if any(a["type"] == "add_collection" and a["value"] == str(collection_id) for a in rule.actions):
            raise HTTPException(409, "请先移除引用此集合的自动分类规则")
    db.delete(collection)
    db.commit()
    return {"ok": True}


def rule_values(body, db):
    condition = validate(body.condition, allow_personal=False)
    actions = [a.model_dump() for a in body.actions]
    for action in actions:
        if action["type"] == "add_collection":
            try:
                id = str(UUID(action["value"]))
            except ValueError as exc:
                raise HTTPException(422, "集合 ID 无效") from exc
            collection = find(db, Collection, id)
            if collection.kind != "static":
                raise HTTPException(422, "规则只能添加到普通集合")
    return {**body.model_dump(exclude={"condition", "actions"}), "condition": condition, "actions": actions}


@router.get("/rules")
def rules(user: User = Depends(admin), db: Session = Depends(get_db)):
    return [{"id": rule.id, "name": rule.name, "condition": rule.condition, "actions": rule.actions,
             "priority": rule.priority, "enabled": rule.enabled} for rule in db.scalars(select(Rule).order_by(Rule.priority))]


@router.post("/rules", status_code=201)
def create_rule(body: s.RuleInput, user: User = Depends(admin), db: Session = Depends(get_db)):
    rule = Rule(**rule_values(body, db))
    db.add(rule)
    db.commit()
    return {"id": rule.id}


@router.patch("/rules/{rule_id}")
def edit_rule(rule_id: UUID, body: s.RuleInput, user: User = Depends(admin), db: Session = Depends(get_db)):
    rule = find(db, Rule, rule_id)
    for key, value in rule_values(body, db).items():
        setattr(rule, key, value)
    db.commit()
    return {"ok": True}


@router.delete("/rules/{rule_id}")
def remove_rule(rule_id: UUID, user: User = Depends(admin), db: Session = Depends(get_db)):
    db.delete(find(db, Rule, rule_id))
    db.execute(delete(AssetTag).where(AssetTag.source == f"rule:{rule_id}"))
    db.execute(delete(CollectionAsset).where(CollectionAsset.source == f"rule:{rule_id}"))
    db.commit()
    return {"ok": True}


@router.post("/rules/apply", status_code=202)
def apply_rules(user: User = Depends(admin), db: Session = Depends(get_db)):
    job = enqueue(db, "classify", {}, user.id, deduplicate=True)
    db.commit()
    return {"job_id": job.id}


@router.get("/jobs")
def jobs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = select(Job).where((Job.kind != "export") | (Job.requested_by == user.id)).order_by(Job.created_at.desc()).limit(100)
    if user.role != "admin":
        query = query.where(Job.kind != "backup")
    return [{"id": j.id, "kind": j.kind, "status": j.status, "progress": j.progress,
             "result": j.result, "error": j.error, "created_at": j.created_at,
             "requested_by": j.requested_by} for j in db.scalars(query)]


@router.post("/jobs/{job_id}/{action}")
def control_job(job_id: UUID, action: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = db.scalar(select(Job).where(Job.id == str(job_id)).with_for_update())
    if not job:
        raise HTTPException(404, "任务不存在")
    if job.requested_by != user.id and user.role != "admin":
        raise HTTPException(403, "只能管理自己的任务")
    if job.kind in ("scan", "reparse", "classify", "backup") and user.role != "admin":
        raise HTTPException(403, "此任务需要管理员权限")
    if action == "retry" and job.status in ("failed", "cancelled"):
        job.status, job.error, job.attempts, job.available_at = "pending", None, 0, now()
        job.lease_owner = None
    elif action == "cancel" and job.status in ("pending", "running"):
        job.status, job.finished_at = "cancelled", now()
    else:
        raise HTTPException(409, "任务状态不支持此操作")
    db.commit()
    return {"ok": True}


@router.get("/jobs/{job_id}/download")
def download_export(job_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = find(db, Job, job_id)
    if job.kind == "backup":
        if user.role != "admin":
            raise HTTPException(403, "只有管理员可以下载系统备份")
    elif job.kind != "export" or job.requested_by != user.id:
        raise HTTPException(403, "只能下载自己生成的导出包")
    path = settings().data_dir / ("backups" if job.kind == "backup" else "exports") / f"{job.id}.zip"
    if job.status != "completed" or not path.is_file():
        raise HTTPException(404, "导出包尚未就绪")
    filename = f"genmedia-{job.kind}-{job.created_at:%Y%m%d-%H%M%S}-{job.id[:8]}.zip"
    return FileResponse(path, media_type="application/zip", filename=filename,
                        headers={"Cache-Control": "private, no-store"})


@router.get("/system")
def system(user: User = Depends(admin)):
    return {"version": "0.3.0", "parser_version": PARSER_VERSION,
            "import_roots": [str(p) for p in settings().import_roots], "max_upload_mb": settings().upload_limit_mb,
            "scan_interval_seconds": settings().scan_interval_seconds, "database": "PostgreSQL",
            "parsers": ["Generic EXIF", "A1111", "NovelAI + stealth", "ComfyUI", "Sidecar JSON/TXT"]}
