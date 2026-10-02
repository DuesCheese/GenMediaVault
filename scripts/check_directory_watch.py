"""Exercise automatic directory discovery/reconciliation using synthetic files in the default test mount."""
import argparse
import hashlib
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
config = dotenv_values(ROOT / ".env")
parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:18081")
parser.add_argument("--timeout", type=int, default=340)
args = parser.parse_args()
mount = (ROOT / "data/imports").resolve()
if (ROOT / config.get("GMV_IMPORT_ROOT", "data/imports")).resolve() != mount:
    raise RuntimeError("Run this synthetic test only with the default project data/imports mount")
name = "_acceptance_" + uuid4().hex[:12]
folder = mount / name
folder.mkdir()
path = folder / "自动发现.png"
fixture = ROOT / "backend/tests/fixtures/novelai.png"


def wait_for(check):
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(1)
    raise AssertionError("Automatic reconciliation timed out")


with httpx.Client(base_url=args.url, timeout=30) as client:
    response = client.post("/api/v1/auth/login", json={"username": config["GMV_ADMIN_USERNAME"], "password": config["GMV_ADMIN_PASSWORD"]})
    response.raise_for_status()
    client.headers["X-CSRF-Token"] = response.json()["csrf"]
    response = client.post("/api/v1/libraries", json={"name": "目录自动化验收", "mode": "indexed", "root_path": "/imports/" + name, "watch_enabled": True})
    response.raise_for_status()
    library_id = response.json()["id"]
    wait_for(lambda: any(lib["id"] == library_id and lib["last_scan_at"] for lib in client.get("/api/v1/libraries").json()))
    shutil.copyfile(fixture, path)
    started = time.monotonic()
    assets = wait_for(lambda: client.post("/api/v1/search", json={"library_id": library_id}).json()["items"])
    discovered_seconds = round(time.monotonic() - started, 2)
    assert len(assets) == 1
    id = assets[0]["id"]
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    assert original_hash == hashlib.sha256(client.get(f"/api/v1/assets/{id}/original").content).hexdigest()
    detail = client.get(f"/api/v1/assets/{id}").json()
    assert detail["generation"]["generator"] == "novelai"
    assert "hair:white" in detail["tags"]
    path.unlink()
    wait_for(lambda: not client.get(f"/api/v1/assets/{id}").json()["files"][0]["present"])
    shutil.copyfile(fixture, path)
    wait_for(lambda: client.get(f"/api/v1/assets/{id}").json()["files"][0]["present"])
    assert len(client.post("/api/v1/search", json={"library_id": library_id}).json()["items"]) == 1
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_hash
    client.patch(f"/api/v1/libraries/{library_id}", json={"name": "目录自动化验收", "watch_enabled": False}).raise_for_status()
    client.post("/api/v1/auth/logout").raise_for_status()
report = {"verified_at": datetime.now(timezone.utc).isoformat(), "automatic_discovery": "passed",
          "discovered_seconds": discovered_seconds, "automatic_classification": "passed",
          "missing_and_returned_source": "passed", "no_duplicate_asset": "passed",
          "source_bytes_preserved": True, "scope": "Watchdog plus periodic reconciliation together; interval is configured by the worker environment"}
(ROOT / "docs/watch-verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report))
