"""Administrator snapshots: PostgreSQL custom dump plus application files and indexed originals."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from sqlalchemy import select, text

from .config import settings
from .db import engine
from .models import Asset, Library, PhysicalFile
from .parsers import sidecars_for
from .services import within


def postgres_env():
    url = engine().url
    return {**os.environ, "PGHOST": url.host or "localhost", "PGPORT": str(url.port or 5432),
            "PGDATABASE": url.database or "genmedia", "PGUSER": url.username or "",
            "PGPASSWORD": url.password or "", "PGCLIENTENCODING": "UTF8"}


def create_snapshot(job, connection, check):
    root = settings().data_dir.resolve()
    destination = root / "backups" / f"{job.id}.zip"
    temporary = destination.with_suffix(".tmp")
    dump = destination.with_suffix(".dump")
    manifest = {"format": "genmedia-snapshot", "version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                "label": job.payload.get("label", ""), "include_indexed": job.payload.get("include_indexed", True),
                "files": [], "indexed": [], "warnings": [], "excluded": ["backup archives", ".env / deployment configuration"]}
    manifest["source_data_root"] = str(root)
    process = None
    try:
        connection.execution_options(isolation_level="REPEATABLE READ")
        with connection.begin():
            snapshot = connection.scalar(text("SELECT pg_export_snapshot()"))
            manifest["migration"] = connection.scalar(text("SELECT version_num FROM alembic_version"))
            check({"phase": "正在备份数据库，工作空间暂时只读"})
            # pg_dump joins the same MVCC snapshot used for the source-file manifest.
            process = subprocess.Popen([settings().pg_dump_binary, "-Fc", "--no-owner", "--no-privileges",
                                        f"--snapshot={snapshot}", "-f", str(dump)], env=postgres_env(),
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
            while process.poll() is None:
                check()
                time.sleep(0.2)
            if process.returncode:
                # Do not include connection configuration/passwords in a user-visible job error.
                raise ValueError("PostgreSQL 备份失败，请检查 pg_dump 版本与数据库连接")
            work = [(dump, "database.dump", None)]
            for directory in ("originals", "attachments", "thumbnails", "staging", "exports"):
                for path in (root / directory).rglob("*"):
                    if path.is_symlink():
                        raise ValueError("应用数据目录包含符号链接，无法保证备份范围")
                    if path.is_file():
                        work.append((path, "data/" + path.relative_to(root).as_posix(), None))
            if manifest["include_indexed"]:
                rows = connection.execute(select(PhysicalFile.id, PhysicalFile.path, PhysicalFile.present,
                    Library.id.label("library_id"), Library.root_path, Asset.sha256, Asset.extension)
                    .join(Asset, Asset.id == PhysicalFile.asset_id).join(Library, Library.id == Asset.library_id)
                    .where(Library.mode == "indexed", PhysicalFile.role == "original")).mappings()
                for row in rows:
                    path = Path(row["path"])
                    if not row["present"] or not path.is_file():
                        manifest["warnings"].append(f"索引源文件缺失：{path}")
                        continue
                    if not within(path, Path(row["root_path"])):
                        raise ValueError("索引源文件越过媒体库边界")
                    # Preserve paths relative to each library; restore can bind a new read-only root.
                    relative = path.resolve().relative_to(Path(row["root_path"]).resolve()).as_posix()
                    archive_name = f"indexed/{row['library_id']}/{relative}"
                    manifest["indexed"].append({"file_id": row["id"], "library_id": row["library_id"],
                                                 "original_path": str(path), "archive_path": archive_name})
                    work.append((path, archive_name, row["sha256"]))
                    for sidecar in sidecars_for(path):
                        if within(sidecar, Path(row["root_path"])):
                            relative_sidecar = sidecar.resolve().relative_to(Path(row["root_path"]).resolve()).as_posix()
                            work.append((sidecar, f"indexed/{row['library_id']}/{relative_sidecar}", None))
            names = set()
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                for index, (path, name, expected) in enumerate(work):
                    check({"phase": "正在打包文件", "completed": index, "total": len(work)})
                    if name in names:
                        continue
                    names.add(name)
                    before, digest = path.stat(), hashlib.sha256()
                    with path.open("rb") as source, archive.open(name, "w", force_zip64=True) as target:
                        while chunk := source.read(1024 * 1024):
                            check()
                            digest.update(chunk)
                            target.write(chunk)
                    after = path.stat()
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ValueError(f"备份期间源文件变化，请重试：{path.name}")
                    if expected and expected != digest.hexdigest():
                        raise ValueError(f"索引源图与入库内容不同，请先重新扫描：{path.name}")
                    manifest["files"].append({"path": name, "size": after.st_size, "sha256": digest.hexdigest()})
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                archive.writestr("RESTORE.txt", "停止应用和 worker，在空数据库与空媒体卷中运行 genmedia restore-backup ARCHIVE.zip。\n详见 docs/OPERATIONS.md。索引库副本恢复到 /data/restored-indexed，可再迁移到只读挂载。\n")
        os.replace(temporary, destination)
        return {"download": f"/api/v1/jobs/{job.id}/download", "size": destination.stat().st_size,
                "files": len(manifest["files"]), "warnings": manifest["warnings"],
                "snapshot_at": manifest["created_at"], "label": manifest["label"]}
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            if process.stderr:
                process.stderr.close()
        dump.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)


def restore_snapshot(archive_path):
    """Offline operator command; never overwrite an initialized database or media directory."""
    root = settings().data_dir.resolve()
    with engine().connect() as connection:
        if connection.scalar(text("SELECT count(*) FROM pg_tables WHERE schemaname='public'")):
            raise ValueError("恢复只允许空数据库，请使用新的 Compose 项目和数据卷")
    if any(path.is_file() or path.is_symlink() for path in root.rglob("*")):
        raise ValueError("恢复只允许空媒体目录，ZIP 请放在单独的只读挂载中")
    with zipfile.ZipFile(archive_path) as archive:
        if archive.getinfo("manifest.json").file_size > 64 * 1024 * 1024:
            raise ValueError("备份清单过大")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format") != "genmedia-snapshot" or manifest.get("version") != 1:
            raise ValueError("不支持此备份格式")
        seen = set()
        for entry in manifest["files"]:
            name = entry["path"]
            path = PurePosixPath(name)
            if name in seen or path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
                raise ValueError("备份包含不安全或重复的路径")
            if name != "database.dump" and path.parts[0] not in ("data", "indexed"):
                raise ValueError("备份目录无效")
            seen.add(name)
            info, digest = archive.getinfo(name), hashlib.sha256()
            if info.file_size != entry["size"] or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("备份文件大小或类型无效")
            with archive.open(name) as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            if digest.hexdigest() != entry["sha256"]:
                raise ValueError(f"备份校验失败：{name}")
        if "database.dump" not in seen:
            raise ValueError("备份缺少数据库")
        with tempfile.TemporaryDirectory(prefix="genmedia-restore-") as temporary:
            dump = Path(temporary) / "database.dump"
            with archive.open("database.dump") as source, dump.open("wb") as output:
                shutil.copyfileobj(source, output)
            executable = str(Path(settings().pg_dump_binary).with_name("pg_restore.exe" if os.name == "nt" else "pg_restore"))
            subprocess.run([executable, "--no-owner", "--no-privileges", "--exit-on-error", "-d", engine().url.database, str(dump)],
                           env=postgres_env(), check=True)
        for entry in manifest["files"]:
            path = PurePosixPath(entry["path"])
            if path.parts[0] == "data":
                target = root.joinpath(*path.parts[1:])
            elif path.parts[0] == "indexed":
                target = root / "restored-indexed" / Path(*path.parts[1:])
            else:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry["path"]) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
    with engine().begin() as connection:
        # Session tokens are deliberately revoked on restore; account/password hashes are retained.
        connection.execute(text("DELETE FROM sessions"))
        connection.execute(text("UPDATE jobs SET status='cancelled', lease_until=NULL, error='恢复后已暂停未完成任务，请核对目录后手动重试' WHERE status IN ('pending','running')"))
        old_root = manifest.get("source_data_root", "/data")
        def relocate(value):
            if isinstance(value, str) and (value.startswith(old_root + "/") or value.startswith(old_root + "\\")):
                return str(root.joinpath(*value[len(old_root) + 1:].replace("\\", "/").split("/")))
            if isinstance(value, dict):
                return {key: relocate(item) for key, item in value.items()}
            if isinstance(value, list):
                return [relocate(item) for item in value]
            return value
        for row in connection.execute(text("SELECT id, path FROM physical_files")).mappings():
            updated = relocate(row["path"])
            if updated != row["path"]:
                connection.execute(text("UPDATE physical_files SET path=:path WHERE id=:id"), {"path": updated, "id": row["id"]})
        for row in connection.execute(text("SELECT id, payload FROM jobs WHERE kind='import'")).mappings():
            connection.execute(text("UPDATE jobs SET payload=CAST(:payload AS jsonb) WHERE id=:id"),
                               {"payload": json.dumps(relocate(row["payload"])), "id": row["id"]})
        for entry in manifest["indexed"]:
            destination = root / "restored-indexed" / Path(*PurePosixPath(entry["archive_path"]).parts[1:])
            connection.execute(text("UPDATE physical_files SET path=:path WHERE id=:id"), {"path": str(destination), "id": entry["file_id"]})
        for library_id in {item["library_id"] for item in manifest["indexed"]}:
            connection.execute(text("UPDATE libraries SET root_path=:root, watch_enabled=false WHERE id=:id"),
                               {"root": str(root / "restored-indexed" / library_id), "id": library_id})
    print("备份已恢复。请启动应用；恢复的索引副本位于 data/restored-indexed，重新登录后核对。")
