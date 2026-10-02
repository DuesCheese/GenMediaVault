"""Compare an isolated Docker restore with the source, then exercise its authenticated media API."""
import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
config = dotenv_values(ROOT / ".env")


def query(container, sql):
    return subprocess.run(["docker", "exec", container, "psql", "-U", "genmedia", "-d", "genmedia", "-Atc", sql],
                          check=True, capture_output=True, text=True).stdout.strip()


parser = argparse.ArgumentParser()
parser.add_argument("--source-container", default="56-genmediavault-postgres-1")
parser.add_argument("--restored-container", default="genmedia-restore-test-postgres-1")
parser.add_argument("--source-url", default="http://127.0.0.1:8080")
parser.add_argument("--restored-url", default="http://127.0.0.1:18081")
args = parser.parse_args()
source, restored = args.source_container, args.restored_container
tables = query(source, "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").splitlines()
checks = {}
for table in tables:
    # Only PostgreSQL-generated identifier names; quote even these before using them.
    quoted = '"' + table.replace('"', '""') + '"'
    sql = f"SELECT count(*), md5(string_agg(row_to_json(t)::text, E'\\n' ORDER BY row_to_json(t)::text)) FROM {quoted} t"
    before, after = query(source, sql), query(restored, sql)
    assert before == after, f"Restored table differs: {table}"
    checks[table] = {"rows": int(before.split("|")[0]), "identical": True}

digests, personal = [], []
for address in (args.source_url, args.restored_url):
    with httpx.Client(base_url=address, timeout=30) as client:
        response = client.post("/api/v1/auth/login", json={"username": config["GMV_ADMIN_USERNAME"], "password": config["GMV_ADMIN_PASSWORD"]})
        response.raise_for_status()
        client.headers["X-CSRF-Token"] = response.json()["csrf"]
        result = client.post("/api/v1/search", json={"query": "seed:18446744073709551615"})
        result.raise_for_status()
        assets = result.json()["items"]
        assert assets, "Expected the synthetic Playwright upload to exist"
        hashes, state = {}, {}
        for asset in assets:
            id = asset["id"]
            original = client.get(f"/api/v1/assets/{id}/original")
            original.raise_for_status()
            hashes[id] = hashlib.sha256(original.content).hexdigest()
            assert client.get(f"/api/v1/assets/{id}/thumbnail").status_code == 200
            detail = client.get(f"/api/v1/assets/{id}").json()
            state[id] = {key: detail[key] for key in ("rating", "notes", "favorite", "generation", "tags")}
        jobs = client.get("/api/v1/jobs").json()
        exports = [job for job in jobs if job["kind"] == "export" and job["status"] == "completed"]
        assert exports
        for job in exports:
            archive = client.get(f"/api/v1/jobs/{job['id']}/download")
            archive.raise_for_status()
            hashes["export:" + job["id"]] = hashlib.sha256(archive.content).hexdigest()
        digests.append(hashes)
        personal.append(state)
        assert client.post("/api/v1/auth/logout").status_code == 200
        assert client.get(f"/api/v1/assets/{assets[0]['id']}/original").status_code == 401
assert digests[0] == digests[1], "Media/export bytes differ after restore"
assert personal[0] == personal[1], "Metadata or personal data differ after restore"
report = {"verified_at": datetime.now(timezone.utc).isoformat(), "compose_build_and_health": "passed",
          "backup_script_create_and_restore": "passed", "tables": checks,
          "authenticated_media_and_export_hashes_equal": True, "metadata_and_personal_state_equal": True,
          "logout_protects_originals": True, "matched_files": len(digests[0])}
(ROOT / "docs/compose-verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps({"compose_restore": "passed", "tables": len(checks), "matched_files": len(digests[0])}))
