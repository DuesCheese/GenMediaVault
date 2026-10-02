import hashlib
import json
import os
import shutil
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps
from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert

from .config import settings
from .models import (Asset, AssetModel, AssetTag, Collection, CollectionAsset, Generation, Job,
                     Library, ModelRef, PhysicalFile, PromptToken, RawMetadata, Rule, Tag, UserAsset, now)
from .parsers import (DICTIONARY, PARSER_VERSION, canonical, extract, normalize,
                      sidecars_for, tokenize_prompt)
from .search import compile_ast


class UnstableFile(ValueError):
    pass


class Cancelled(Exception):
    pass


def within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def allowed_import(path: str) -> Path:
    resolved = Path(path).resolve()
    if not any(within(resolved, root) for root in settings().import_roots):
        raise ValueError("目录不在允许的导入挂载范围内")
    if not resolved.is_dir():
        raise ValueError("导入目录不存在或不可读取")
    return resolved


def checked_original(file: PhysicalFile, library: Library) -> Path:
    path = Path(file.path).resolve()
    root = Path(library.root_path) if library.mode == "indexed" else settings().data_dir / "originals"
    if not within(path, root):
        raise ValueError("原文件路径越过媒体库边界")
    return path


def enqueue(db, kind: str, payload: dict, user_id=None, deduplicate=False):
    if deduplicate:
        key = f"{kind}:{json.dumps(payload, sort_keys=True)}"
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})
        existing = db.scalar(select(Job).where(Job.kind == kind, Job.payload == payload,
                                               Job.status.in_(["pending", "running"])))
        if existing:
            return existing
    job = Job(kind=kind, payload=payload, requested_by=user_id)
    db.add(job)
    db.flush()
    return job


def add_tag(db, asset_id, name, source="user"):
    name = canonical(name)
    if not name or len(name) > 200:
        raise ValueError("标签需要 1–200 个字符")
    db.execute(insert(Tag).values(name=name, namespace=name.partition(":")[0] if ":" in name else "")
               .on_conflict_do_nothing(index_elements=[Tag.name]))
    tag_id = db.scalar(select(Tag.id).where(Tag.name == name))
    db.execute(insert(AssetTag).values(asset_id=asset_id, tag_id=tag_id, source=source)
               .on_conflict_do_nothing())


def add_collection(db, asset_id, collection_id, source="user"):
    collection = db.get(Collection, collection_id)
    if not collection or collection.kind != "static":
        raise ValueError("只能添加到存在的普通集合")
    db.execute(insert(CollectionAsset).values(asset_id=asset_id, collection_id=collection_id, source=source)
               .on_conflict_do_nothing())


def classify(db, asset: Asset):
    db.execute(delete(AssetTag).where(AssetTag.asset_id == asset.id, AssetTag.source != "user"))
    db.execute(delete(CollectionAsset).where(CollectionAsset.asset_id == asset.id,
                                             CollectionAsset.source != "user"))
    generation = db.get(Generation, asset.id)
    if generation:
        add_tag(db, asset.id, f"generator:{generation.generator}", "metadata")
    orientation = "square" if asset.width == asset.height else "portrait" if asset.width < asset.height else "landscape"
    add_tag(db, asset.id, f"orientation:{orientation}", "system")
    for token in db.scalars(select(PromptToken).where(PromptToken.asset_id == asset.id,
                                                     PromptToken.polarity == "positive")):
        if token.token in DICTIONARY:
            add_tag(db, asset.id, DICTIONARY[token.token], "prompt")
    # Stable priority order; rules only add shared data and never write personal state.
    for rule in db.scalars(select(Rule).where(Rule.enabled.is_(True)).order_by(Rule.priority, Rule.id)):
        if db.scalar(select(Asset.id).where(Asset.id == asset.id, compile_ast(rule.condition, "00000000-0000-0000-0000-000000000000"))):
            for action in rule.actions:
                if action["type"] == "add_tag":
                    add_tag(db, asset.id, action["value"], f"rule:{rule.id}")
                else:
                    add_collection(db, asset.id, action["value"], f"rule:{rule.id}")


def apply_metadata(db, asset: Asset, bundle: dict, source: str, replace=True):
    parsed = normalize(bundle)
    # Replaying an interrupted job must not create identical evidence snapshots.
    known = db.scalar(select(RawMetadata.id).where(RawMetadata.asset_id == asset.id,
                      RawMetadata.source == source, RawMetadata.parser_version == PARSER_VERSION,
                      RawMetadata.data == bundle).limit(1))
    if not known:
        db.add(RawMetadata(asset_id=asset.id, source=source, parser=parsed.parser,
                           parser_version=PARSER_VERSION, data=bundle, warnings=parsed.warnings))
    existing = db.get(Generation, asset.id)
    if existing and not replace:
        conflicts = list(existing.conflicts)
        for key, value in parsed.normalized.items():
            previous = existing.normalized.get(key)
            if previous != value and value is not None and value != "":
                conflicts.append({"field": key, "previous": previous, "incoming": value,
                                  "incoming_source": source, "resolved_source": "existing"})
        existing.conflicts = conflicts[-100:]
        return
    normalized = parsed.normalized
    columns = {key: normalized.get(key) for key in
               ("generator", "model", "model_hash", "prompt", "negative", "seed", "steps", "cfg", "sampler", "scheduler")}
    columns["generator"] = columns["generator"] or "unknown"
    db.execute(insert(Generation).values(asset_id=asset.id, **columns, normalized=normalized,
                                         conflicts=parsed.conflicts, parsed_at=now())
               .on_conflict_do_update(index_elements=[Generation.asset_id],
                                      set_={**columns, "normalized": normalized,
                                            "conflicts": parsed.conflicts, "parsed_at": now()}))
    if existing:
        db.expire(existing)
    db.execute(delete(PromptToken).where(PromptToken.asset_id == asset.id))
    for polarity, key in (("positive", "prompt"), ("negative", "negative")):
        for token in tokenize_prompt(normalized.get(key, ""), normalized.get("generator", "unknown"))[:5000]:
            db.add(PromptToken(asset_id=asset.id, polarity=polarity, **token))
    db.execute(delete(AssetModel).where(AssetModel.asset_id == asset.id))
    references = ([{"kind": "model", "name": normalized["model"]}] if normalized.get("model") else [])
    references += [{**ref, "kind": "lora"} for ref in normalized.get("loras", []) if isinstance(ref, dict)]
    for ref in references:
        if not isinstance(ref.get("name"), str) or not ref["name"]:
            continue
        db.execute(insert(ModelRef).values(kind=ref["kind"], name=ref["name"])
                   .on_conflict_do_nothing(index_elements=[ModelRef.kind, ModelRef.name]))
        model_id = db.scalar(select(ModelRef.id).where(ModelRef.kind == ref["kind"], ModelRef.name == ref["name"]))
        weight = ref.get("weight")
        db.execute(insert(AssetModel).values(asset_id=asset.id, model_id=model_id,
                                             weight=weight if isinstance(weight, (int, float)) else None)
                   .on_conflict_do_nothing())
    asset.warnings = parsed.warnings
    db.flush()
    classify(db, asset)


def fingerprint(paths):
    return json.dumps([(p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in paths], ensure_ascii=False)


def thumbnail(asset_id, path):
    target = settings().data_dir / "thumbnails" / f"{asset_id}.webp"
    temporary = target.with_suffix(".tmp")
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail((640, 640))
        image.convert("RGB").save(temporary, "WEBP", quality=82)
    os.replace(temporary, target)


def ingest(db, library: Library, path: Path, filename=None, uploaded=False, explicit_sidecars=None):
    path = path.resolve()
    if library.mode == "indexed" and not within(path, Path(library.root_path)):
        raise ValueError("文件路径越过媒体库边界")
    before = path.stat()
    if not uploaded and time.time() - before.st_mtime < settings().stability_seconds:
        raise UnstableFile("文件仍在写入，稍后重试")
    previous = db.scalar(select(PhysicalFile).where(PhysicalFile.library_id == library.id,
                                                   PhysicalFile.path == str(path), PhysicalFile.role == "original"))
    if previous:
        asset = db.get(Asset, previous.asset_id)
        if asset.trashed_at is not None:
            return {"status": "excluded", "asset_id": asset.id}
    sidecars = explicit_sidecars if explicit_sidecars is not None else sidecars_for(path)
    if library.mode == "indexed":
        sidecars = [p for p in sidecars if within(p, Path(library.root_path))]
    stamp = fingerprint(sidecars)
    if previous and previous.size == before.st_size and previous.mtime_ns == before.st_mtime_ns and previous.sidecar_fingerprint == stamp:
        previous.present = True
        return {"status": "unchanged", "asset_id": previous.asset_id}
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    checksum = digest.hexdigest()
    bundle = extract(path, sidecars)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or stamp != fingerprint(sidecars):
        raise UnstableFile("读取期间文件发生变化，稍后重试")
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
               {"key": f"asset:{library.id}:{checksum}"})
    asset = db.scalar(select(Asset).where(Asset.library_id == library.id, Asset.sha256 == checksum))
    duplicate = asset is not None
    if asset and asset.trashed_at is not None:
        return {"status": "excluded", "asset_id": asset.id}
    technical = bundle["technical"]
    if not asset:
        extension = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[technical["format"]]
        asset = Asset(library_id=library.id, sha256=checksum, filename=filename or path.name,
                      extension=extension, mime_type=f"image/{technical['format'].lower()}",
                      width=technical["width"], height=technical["height"], file_size=before.st_size,
                      source_created_at=datetime.fromtimestamp(before.st_mtime, timezone.utc))
        db.add(asset)
        db.flush()
    original_source = str(path)
    if library.mode == "managed":
        destination = settings().data_dir / "originals" / library.id / checksum[:2] / f"{checksum}{asset.extension}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            temporary = destination.with_suffix(".tmp")
            shutil.copyfile(path, temporary)
            os.replace(temporary, destination)
        path = destination.resolve()
        # Preserve sidecars alongside the managed original without overwriting earlier conflicting evidence.
        for sidecar in sidecars:
            sidecar_target = Path(str(path) + sidecar.suffix)
            if not sidecar_target.exists():
                shutil.copyfile(sidecar, sidecar_target)
    current = path.stat()
    if previous and previous.asset_id != asset.id:
        previous.present = False
        # Keep the historic location record while releasing its active path uniqueness.
        previous.path = previous.path + f"#replaced:{previous.id}"
        db.flush()
    file = db.scalar(select(PhysicalFile).where(PhysicalFile.library_id == library.id,
                                               PhysicalFile.path == str(path), PhysicalFile.role == "original"))
    if not file:
        file = PhysicalFile(asset_id=asset.id, library_id=library.id, path=str(path), role="original")
        db.add(file)
    file.size, file.mtime_ns, file.present = current.st_size, current.st_mtime_ns, True
    file.sidecar_fingerprint = stamp
    # Every upload records its original filename, including deduplicated uploads.
    bundle["origin"] = {"filename": filename or Path(original_source).name, "source": original_source}
    # A newly discovered sidecar is evidence, not permission to replace existing parameters.
    # Administrators can explicitly reparse after reviewing the incoming conflicts.
    apply_metadata(db, asset, bundle, str(path), replace=not duplicate)
    try:
        thumbnail(asset.id, path)
    except (OSError, ValueError) as exc:
        asset.warnings = list(asset.warnings) + [f"缩略图生成失败：{exc}"]
    asset.status = "ready"
    db.flush()
    return {"status": "duplicate" if duplicate else "imported", "asset_id": asset.id}


def reparse_asset(db, asset: Asset):
    library = db.get(Library, asset.library_id)
    file = db.scalar(select(PhysicalFile).where(PhysicalFile.asset_id == asset.id,
                                               PhysicalFile.role == "original")
                      .order_by(PhysicalFile.created_at, PhysicalFile.id))
    path = checked_original(file, library) if file else None
    if path and path.is_file():
        before = path.stat()
        root = Path(library.root_path) if library.mode == "indexed" else settings().data_dir / "originals"
        sidecars = [p for p in sidecars_for(path) if within(p, root)]
        stamp = fingerprint(sidecars)
        bundle = extract(path, sidecars)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or stamp != fingerprint(sidecars):
            raise UnstableFile("原文件在解析时变化")
        # A changed indexed file belongs to a new asset, never overwrite the old content identity.
        with path.open("rb") as stream:
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != asset.sha256:
            raise ValueError("原文件内容已变化，请重新扫描媒体库")
        apply_metadata(db, asset, bundle, str(path))
        try:
            thumbnail(asset.id, path)
        except (OSError, ValueError) as exc:
            asset.warnings = list(asset.warnings) + [f"缩略图生成失败：{exc}"]
    else:
        query = select(RawMetadata).where(RawMetadata.asset_id == asset.id)
        if file:
            query = query.where(RawMetadata.source == file.path)
        raw = db.scalar(query.order_by(RawMetadata.created_at.desc(), RawMetadata.id.desc()))
        if not raw:
            raise ValueError("原文件和原始元数据均不可用")
        apply_metadata(db, asset, raw.data, raw.source)
        asset.warnings = list(asset.warnings) + ["原文件缺失，仅从已保存的元数据重新标准化；新增提取能力未运行"]


def export_assets(db, job, check):
    target = settings().data_dir / "exports" / f"{job.id}.zip"
    temporary = target.with_suffix(".tmp")
    manifest = []
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for index, asset_id in enumerate(job.payload["asset_ids"]):
            check({"completed": index, "total": len(job.payload["asset_ids"])})
            asset = db.get(Asset, asset_id)
            if not asset or asset.trashed_at is not None:
                manifest.append({"id": asset_id, "error": "资产已删除"})
                continue
            files = db.scalars(select(PhysicalFile).where(PhysicalFile.asset_id == asset.id,
                                                          PhysicalFile.role == "original", PhysicalFile.present.is_(True)))
            library = db.get(Library, asset.library_id)
            path = next((p for f in files if (p := checked_original(f, library)).is_file()), None)
            base = f"{asset.id}/{asset.id}"
            if path:
                archive.write(path, base + asset.extension)
            generation = db.get(Generation, asset.id)
            personal = db.get(UserAsset, (job.requested_by, asset.id)) if job.requested_by else None
            metadata = {"schema_version": 1, "filename": asset.filename,
                        "generation": generation.normalized if generation else {},
                        "tags": list(db.scalars(select(Tag.name).join(AssetTag).where(AssetTag.asset_id == asset.id).distinct())),
                        "personal": {"rating": personal.rating, "favorite": personal.favorite,
                                     "notes": personal.notes, "review": personal.review} if personal else {},
                        "raw": [{"source": raw.source, "data": raw.data} for raw in db.scalars(
                            select(RawMetadata).where(RawMetadata.asset_id == asset.id))]}
            archive.writestr(base + asset.extension + ".json", json.dumps(metadata, ensure_ascii=False, indent=2))
            archive.writestr(base + ".txt", generation.prompt if generation else "")
            manifest.append({"id": asset.id, "original_included": path is not None})
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    os.replace(temporary, target)
    return {"download": f"/api/v1/jobs/{job.id}/download", "count": len(manifest)}
