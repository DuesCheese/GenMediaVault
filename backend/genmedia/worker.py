import logging
import threading
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import or_, select, update
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .config import settings
from .db import session_factory
from .maintenance import mutation_gate, update_in_progress
from .models import Asset, Generation, Job, Library, PhysicalFile, now
from .parsers import EXTENSIONS, sidecars_for
from .services import (Cancelled, UnstableFile, classify, enqueue, export_assets, ingest,
                       reparse_asset, within)

log = logging.getLogger("genmedia.worker")


def claim():
    if update_in_progress():
        return None
    with session_factory()() as db:
        job = db.scalar(select(Job).where(or_(
            (Job.status == "pending") & (Job.available_at <= now()),
            (Job.status == "running") & (Job.lease_until < now()),
        )).order_by(Job.created_at).with_for_update(skip_locked=True).limit(1))
        if not job:
            return None
        if job.attempts >= 3:
            job.status, job.error = "failed", "任务租约多次过期，请检查 worker 日志后手动重试"
            db.commit()
            return None
        job.status, job.lease_owner = "running", str(uuid4())
        job.lease_until, job.attempts = now() + timedelta(seconds=settings().lease_seconds), job.attempts + 1
        db.commit()
        return job


def job_check(job, progress=None):
    with session_factory()() as state:
        current = state.get(Job, job.id)
        if not current or current.status != "running" or current.lease_owner != job.lease_owner:
            raise Cancelled()
        if progress is not None:
            current.progress = progress
            state.commit()


def execute(job):
    def check(progress=None):
        job_check(job, progress)

    with session_factory()() as db:
        if job.kind == "scan":
            library = db.get(Library, job.payload["library_id"])
            if not library or library.mode != "indexed":
                raise ValueError("索引库不存在")
            root = Path(library.root_path)
            if not root.is_dir():
                raise ValueError("索引目录不可用；请检查挂载，未将全部资产标记为缺失")
            counts = {"completed": 0, "imported": 0, "duplicate": 0, "unchanged": 0,
                      "excluded": 0, "failed": 0, "deferred": 0, "errors": []}
            deferred = []
            for path in root.rglob("*"):
                if path.suffix.lower() not in EXTENSIONS or not path.is_file() or not within(path, root):
                    continue
                check(counts)
                try:
                    result = ingest(db, library, path)
                    db.commit()
                    counts[result["status"]] += 1
                except UnstableFile:
                    db.rollback()
                    counts["deferred"] += 1
                    deferred.append(str(path))
                except (ValueError, OSError) as exc:
                    db.rollback()
                    counts["failed"] += 1
                    if len(counts["errors"]) < 100:
                        counts["errors"].append({"file": str(path), "error": str(exc)})
                counts["completed"] += 1
            for file in db.scalars(select(PhysicalFile).where(PhysicalFile.library_id == library.id,
                                                             PhysicalFile.role == "original")):
                file.present = Path(file.path).is_file()
            library.last_scan_at = now()
            if deferred:
                retry = enqueue(db, "scan", {"library_id": library.id}, job.requested_by)
                retry.available_at = now() + timedelta(seconds=10)
            db.commit()
            check(counts)
            return counts
        if job.kind == "import":
            library = db.get(Library, job.payload["library_id"])
            counts = {"completed": 0, "total": len(job.payload["files"]), "imported": 0,
                      "duplicate": 0, "excluded": 0, "unchanged": 0, "failed": 0, "errors": []}
            for item in job.payload["files"]:
                check(counts)
                path = Path(item["path"])
                if not within(path, settings().data_dir / "staging"):
                    raise ValueError("上传暂存路径无效")
                try:
                    result = ingest(db, library, path, item["name"], uploaded=True,
                                    explicit_sidecars=sidecars_for(path), uploader_id=job.requested_by)
                    db.commit()
                    counts[result["status"]] += 1
                except (ValueError, OSError) as exc:
                    db.rollback()
                    counts["failed"] += 1
                    counts["errors"].append({"file": item["name"], "error": str(exc)})
                counts["completed"] += 1
            check(counts)
            return counts
        if job.kind == "export":
            return export_assets(db, job, check)
        if job.kind in ("reparse", "classify"):
            ids = job.payload.get("asset_ids")
            if ids is None:
                # Bounded pages: do not load the full media library into memory.
                last = None
                ids = []
                while True:
                    query = select(Asset.id).where(Asset.trashed_at.is_(None)).order_by(Asset.id).limit(500)
                    if job.payload.get("generator"):
                        query = query.join(Generation).where(Generation.generator == job.payload["generator"])
                    if last:
                        query = query.where(Asset.id > last)
                    page = list(db.scalars(query))
                    if not page:
                        break
                    ids.extend(page)
                    last = page[-1]
            errors = []
            for index, asset_id in enumerate(ids):
                check({"completed": index, "total": len(ids), "errors": errors[:100]})
                asset = db.get(Asset, asset_id)
                if not asset or asset.trashed_at:
                    continue
                try:
                    if job.kind == "reparse":
                        reparse_asset(db, asset)
                    else:
                        classify(db, asset)
                    db.commit()
                except (ValueError, OSError) as exc:
                    db.rollback()
                    errors.append({"asset_id": asset_id, "error": str(exc)})
            return {"completed": len(ids), "failed": len(errors), "errors": errors[:100]}
        raise ValueError(f"未知任务类型：{job.kind}")


def run_one():
    job = claim()
    if not job:
        return False
    stopped = threading.Event()

    def heartbeat():
        while not stopped.wait(max(1, settings().lease_seconds / 3)):
            try:
                with session_factory()() as db:
                    db.execute(update(Job).where(Job.id == job.id, Job.lease_owner == job.lease_owner,
                                                 Job.status == "running").values(
                        lease_until=now() + timedelta(seconds=settings().lease_seconds)))
                    db.commit()
            except Exception:
                log.exception("Task heartbeat failed")

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    status, result, error = "completed", {}, None
    try:
        with mutation_gate(lambda: job_check(job), exclusive=job.kind == "backup") as gate:
            if job.kind == "backup":
                from .snapshots import create_snapshot
                result = create_snapshot(job, gate, lambda progress=None: job_check(job, progress))
            else:
                result = execute(job)
        if result.get("failed", 0):
            status, error = "failed", f"{result['failed']} 个文件处理失败，成功项目已保留；可重试"
    except Cancelled:
        status = "cancelled"
    except Exception as exc:
        log.exception("Job %s failed", job.id)
        status, error = "failed", str(exc)[:2000]
    finally:
        stopped.set()
        thread.join(timeout=5)
    with session_factory()() as db:
        updated = db.execute(update(Job).where(Job.id == job.id, Job.lease_owner == job.lease_owner,
                                     Job.status == "running").values(status=status, result=result,
                                        error=error, finished_at=now(), lease_until=None))
        db.commit()
        finished_here = updated.rowcount == 1
    if finished_here and status == "completed" and job.kind == "import":
        # Completed uploads no longer need their request-specific temporary files.
        # Failed/cancelled jobs retain staging so Retry remains usable.
        with mutation_gate(lambda: None):
            directories = {Path(item["path"]).parent for item in job.payload["files"]}
            for directory in directories:
                if directory.parent.resolve() != (settings().data_dir / "staging").resolve():
                    continue
                try:
                    for file in directory.iterdir():
                        if file.is_file() and not file.is_symlink():
                            file.unlink()
                    directory.rmdir()
                except OSError:
                    log.warning("Completed staging directory could not be cleaned: %s", directory)
    return True


class Events(FileSystemEventHandler):
    def __init__(self, library_id, dirty, lock):
        self.library_id, self.dirty, self.lock = library_id, dirty, lock

    def on_any_event(self, event):
        if event.event_type in ("opened", "closed_no_write"):
            return
        with self.lock:
            self.dirty[self.library_id] = time.monotonic()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings().prepare()
    observer, dirty, lock, watches = Observer(), {}, threading.Lock(), {}
    observer.start()
    refreshed = 0.0
    try:
        while True:
            if update_in_progress():
                time.sleep(0.5)
                continue
            if time.monotonic() - refreshed > 10:
                with session_factory()() as db:
                    libraries = list(db.scalars(select(Library).where(Library.mode == "indexed",
                                                                     Library.watch_enabled.is_(True))))
                    active = {library.id for library in libraries}
                    for library_id in set(watches) - active:
                        observer.unschedule(watches.pop(library_id))
                    for library in libraries:
                        if library.id not in watches:
                            try:
                                watches[library.id] = observer.schedule(Events(library.id, dirty, lock),
                                                                        library.root_path, recursive=True)
                            except OSError:
                                log.warning("Watch unavailable for %s; periodic scanning remains enabled", library.name)
                        with lock:
                            event_at = dirty.get(library.id)
                            changed = event_at is not None and time.monotonic() - event_at > settings().stability_seconds
                            if changed:
                                dirty.pop(library.id, None)
                        due = not library.last_scan_at or (now() - library.last_scan_at).total_seconds() >= settings().scan_interval_seconds
                        if changed or due:
                            enqueue(db, "scan", {"library_id": library.id}, deduplicate=True)
                    db.commit()
                refreshed = time.monotonic()
            if not run_one():
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join(timeout=5)
