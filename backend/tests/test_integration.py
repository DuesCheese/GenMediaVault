import io
import json
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest
from PIL import Image, PngImagePlugin
from sqlalchemy import func, select

from genmedia.config import settings
from genmedia.models import Asset, Job, Library, RawMetadata, User, now
from genmedia.search import parse
from genmedia.services import UnstableFile, ingest
from genmedia.worker import claim, run_one

pytestmark = pytest.mark.integration


def image_bytes(prompt="white hair, blue eyes", seed="18446744073709551615"):
    output = io.BytesIO()
    info = PngImagePlugin.PngInfo()
    info.add_text("parameters", f"{prompt}\nNegative prompt: bad hands\nSteps: 28, Sampler: Euler, CFG scale: 5, Seed: {seed}, Model: portrait-xl")
    Image.new("RGB", (120, 160), "#b8bba6").save(output, format="PNG", pnginfo=info)
    return output.getvalue()


def upload(client, prompt="white hair, blue eyes", seed="18446744073709551615", sidecar=None):
    library_id = client.get("/api/v1/libraries").json()[0]["id"]
    files = [("files", ("测试图片.png", image_bytes(prompt, seed), "image/png"))]
    if sidecar:
        files.append(("files", ("测试图片.png.json", json.dumps(sidecar).encode(), "application/json")))
    response = client.post("/api/v1/imports", data={"library_id": library_id}, files=files)
    assert response.status_code == 202, response.text
    assert run_one()
    jobs = client.get("/api/v1/jobs").json()
    job = next(j for j in jobs if j["id"] == response.json()["job_id"])
    assert job["status"] == "completed", job
    found = client.post("/api/v1/search", json={}).json()
    assert found["items"], found
    return found["items"][0]["id"]


def test_upload_search_copy_and_source_tags(clients):
    client = clients["alice"]
    id = upload(client)
    detail = client.get(f"/api/v1/assets/{id}").json()
    assert detail["generation"]["seed"] == "18446744073709551615"
    assert "hair:white" in detail["tags"]
    query = 'generator:a1111 AND cfg:4..6 AND prompt.tag:"white hair" AND NOT model:missing'
    found = client.post("/api/v1/search", json={"query": query, "facets": True})
    assert found.status_code == 200, found.text
    assert found.json()["facets"]["total"] == 1
    ast = client.post("/api/v1/search/parse", json={"query": query}).json()["ast"]
    assert client.post("/api/v1/search", json={"ast": ast}).json()["items"][0]["id"] == id
    assert client.get(f"/api/v1/assets/{id}/original").content == image_bytes()
    assert client.get(f"/api/v1/assets/{id}/thumbnail").status_code == 200


def test_personal_state_and_protected_media(clients):
    alice, bob = clients["alice"], clients["bob"]
    id = upload(alice)
    assert alice.patch(f"/api/v1/assets/{id}/personal", json={"rating": 5, "favorite": True, "notes": "private"}).status_code == 200
    assert len(alice.post("/api/v1/search", json={"query": "rating:>=4"}).json()["items"]) == 1
    assert bob.post("/api/v1/search", json={"query": "rating:>=4"}).json()["items"] == []
    for query in ("rating:missing", "NOT rating:>=4", "favorite:false", "review:unreviewed"):
        assert bob.post("/api/v1/search", json={"query": query}).json()["items"][0]["id"] == id
    for query in ("rating:missing", "favorite:false", "favorite:missing"):
        assert alice.post("/api/v1/search", json={"query": query}).json()["items"] == []
    assert bob.get(f"/api/v1/assets/{id}").json()["notes"] == ""
    exported = alice.post("/api/v1/assets/bulk", json={"asset_ids": [id], "action": "export"}).json()["job_id"]
    assert run_one()
    assert bob.get(f"/api/v1/jobs/{exported}/download").status_code == 403
    archive = zipfile.ZipFile(io.BytesIO(alice.get(f"/api/v1/jobs/{exported}/download").content))
    metadata = json.loads(archive.read(f"{id}/{id}.png.json"))
    assert metadata["personal"]["notes"] == "private"
    assert alice.post("/api/v1/auth/logout").status_code == 200
    for suffix in ("original", "thumbnail", "workflow"):
        assert alice.get(f"/api/v1/assets/{id}/{suffix}").status_code == 401


def test_numeric_missing_and_date_comparisons(clients):
    client = clients["alice"]
    id = upload(client)
    for field in ("cfg", "steps"):
        missing = client.post("/api/v1/search", json={"query": f"{field}:missing"})
        assert missing.status_code == 200, missing.text
        assert missing.json()["items"] == []
        assert client.post("/api/v1/search", json={"query": f"NOT {field}:missing"}).json()["items"][0]["id"] == id
    parsed = client.post("/api/v1/search/parse", json={"query": 'created:>="2000-01-01"'}).json()
    result = client.post("/api/v1/search", json={"ast": parsed["ast"]})
    assert result.status_code == 200, result.text
    assert result.json()["items"][0]["id"] == id


def test_permissions_csrf_and_validation(clients):
    alice, admin = clients["alice"], clients["admin"]
    assert alice.post("/api/v1/libraries", json={"name": "bad", "mode": "managed"}).status_code == 403
    assert admin.post("/api/v1/libraries", json={"name": "outside", "mode": "indexed", "root_path": str(Path.home())}).status_code == 422
    csrf = alice.headers.pop("X-CSRF-Token")
    assert alice.post("/api/v1/collections", json={"name": "test"}).status_code == 403
    alice.headers["X-CSRF-Token"] = csrf
    assert alice.post("/api/v1/collections", json={"name": "test"}, headers={"Origin": "https://other.example"}).status_code == 403
    assert alice.post("/api/v1/search", json={"query": "cfg:abc"}).status_code == 422


def test_duplicate_sidecars_reparse_and_trash(clients, database):
    admin = clients["admin"]
    id = upload(admin, sidecar={"schema_version": 1, "generation": {"seed": "456"}})
    upload(admin, sidecar={"schema_version": 1, "generation": {"seed": "789"}})
    with database() as db:
        assert db.scalar(select(func.count()).select_from(Asset)) == 1
        assert db.scalar(select(func.count()).select_from(RawMetadata)) == 2
    detail = admin.get(f"/api/v1/assets/{id}").json()
    assert detail["generation"]["seed"] == "456"
    assert any(c.get("incoming") == "789" for c in detail["conflicts"])
    admin.post("/api/v1/assets/bulk", json={"asset_ids": [id], "action": "add_tag", "value": "user:keep"})
    admin.post("/api/v1/assets/bulk", json={"asset_ids": [id], "action": "reparse"})
    run_one()
    assert "user:keep" in admin.get(f"/api/v1/assets/{id}").json()["tags"]
    assert clients["alice"].post("/api/v1/assets/bulk", json={"asset_ids": [id], "action": "trash"}).status_code == 403
    admin.post("/api/v1/assets/bulk", json={"asset_ids": [id], "action": "trash"})
    assert admin.post("/api/v1/search", json={}).json()["items"] == []
    assert len(admin.post("/api/v1/search", json={"trash": True}).json()["items"]) == 1
    admin.post("/api/v1/assets/bulk", json={"asset_ids": [id], "action": "restore"})
    assert len(admin.post("/api/v1/search", json={}).json()["items"]) == 1


def test_indexed_scan_exclusion_missing_and_recovery(clients, database):
    admin = clients["admin"]
    root = settings().import_roots[0] / "中文目录"
    root.mkdir(exist_ok=True)
    path = root / "源图.png"
    path.write_bytes(image_bytes())
    created = admin.post("/api/v1/libraries", json={"name": "index", "mode": "indexed", "root_path": str(root)})
    assert created.status_code == 201, created.text
    library_id = created.json()["id"]
    assert run_one()
    id = admin.post("/api/v1/search", json={"library_id": library_id}).json()["items"][0]["id"]
    Path(str(path) + ".json").write_text(json.dumps({"schema_version": 1, "generation": {"seed": "42"}}))
    admin.post(f"/api/v1/libraries/{library_id}/scan")
    run_one()
    detail = admin.get(f"/api/v1/assets/{id}").json()
    assert detail["generation"]["seed"] == "18446744073709551615"
    assert any(c.get("incoming") == "42" for c in detail["conflicts"])
    admin.post("/api/v1/assets/bulk", json={"asset_ids": [id], "action": "reparse"})
    run_one()
    assert admin.get(f"/api/v1/assets/{id}").json()["generation"]["seed"] == "42"
    admin.post("/api/v1/assets/bulk", json={"asset_ids": [id], "action": "trash"})
    admin.post(f"/api/v1/libraries/{library_id}/scan")
    run_one()
    assert path.read_bytes() == image_bytes()
    assert admin.post("/api/v1/search", json={"library_id": library_id}).json()["items"] == []
    admin.post("/api/v1/assets/bulk", json={"asset_ids": [id], "action": "restore"})
    path.unlink()
    admin.post(f"/api/v1/libraries/{library_id}/scan")
    run_one()
    admin.post("/api/v1/assets/bulk", json={"asset_ids": [id], "action": "reparse"})
    run_one()
    detail = admin.get(f"/api/v1/assets/{id}").json()
    assert any("原文件缺失" in w for w in detail["warnings"])
    assert not detail["files"][0]["present"]


def test_rule_smart_collection_and_cursor(clients):
    admin, alice = clients["admin"], clients["alice"]
    first = upload(alice, seed="1")
    second = upload(alice, seed="2")
    rule = admin.post("/api/v1/rules", json={"name": "portraits", "condition": parse('prompt.tag:"white hair"'),
                                           "actions": [{"type": "add_tag", "value": "custom:portrait"}]})
    assert rule.status_code == 201, rule.text
    admin.post("/api/v1/rules/apply")
    run_one()
    assert "custom:portrait" in alice.get(f"/api/v1/assets/{first}").json()["tags"]
    alice.patch(f"/api/v1/assets/{first}/personal", json={"rating": 5})
    collection = alice.post("/api/v1/collections", json={"name": "best", "kind": "smart", "query": "rating:>=4"}).json()["id"]
    assert len(alice.post("/api/v1/search", json={"collection_id": collection}).json()["items"]) == 1
    assert clients["bob"].post("/api/v1/search", json={"collection_id": collection}).json()["items"] == []
    page = alice.post("/api/v1/search", json={"limit": 1}).json()
    next_page = alice.post("/api/v1/search", json={"limit": 1, "cursor": page["next_cursor"]}).json()
    assert {page["items"][0]["id"], next_page["items"][0]["id"]} == {first, second}
    assert next_page["next_cursor"] is None
    assert alice.post("/api/v1/search", json={"limit": 1, "cursor": page["next_cursor"], "query": "seed:1"}).status_code == 400


def test_expired_lease_and_cancel(clients, database):
    with database() as db:
        admin_id = db.scalar(select(User.id).where(User.username == "admin"))
        job = Job(kind="classify", payload={"asset_ids": []}, requested_by=admin_id,
                  status="running", lease_until=now() - timedelta(seconds=5), attempts=1, lease_owner="old-owner")
        db.add(job)
        db.commit()
        id = job.id
    claimed = claim()
    assert claimed.id == id and claimed.lease_owner != "old-owner" and claimed.attempts == 2
    assert clients["admin"].post(f"/api/v1/jobs/{id}/cancel").status_code == 200
    assert clients["admin"].post(f"/api/v1/jobs/{id}/retry").status_code == 200
    assert run_one()
    with database() as db:
        assert db.get(Job, id).status == "completed"


def test_unstable_file_and_malformed_upload(clients, database):
    root = settings().import_roots[0]
    path = root / "writing.png"
    path.write_bytes(image_bytes())
    config = settings()
    old = config.stability_seconds
    config.stability_seconds = 60
    try:
        with database() as db:
            library = Library(name="unstable", mode="indexed", root_path=str(root))
            db.add(library)
            db.flush()
            with pytest.raises(UnstableFile):
                ingest(db, library, path)
            db.rollback()
    finally:
        config.stability_seconds = old
        path.unlink()
    client = clients["alice"]
    library_id = client.get("/api/v1/libraries").json()[0]["id"]
    response = client.post("/api/v1/imports", data={"library_id": library_id}, files={"files": ("bad.png", b"not an image", "image/png")})
    run_one()
    job = next(j for j in client.get("/api/v1/jobs").json() if j["id"] == response.json()["job_id"])
    assert job["status"] == "failed" and job["result"]["failed"] == 1
