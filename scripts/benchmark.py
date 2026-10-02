"""Reproducible HTTP/ASGI benchmark on a dedicated *_bench PostgreSQL database.

Synthetic metadata only: this does not measure disk import or thumbnail throughput.
"""
import argparse
import concurrent.futures
import json
import os
import platform
import statistics
import time
from pathlib import Path

from dev_database import environment

parser = argparse.ArgumentParser()
parser.add_argument("--assets", type=int, default=100000)
parser.add_argument("--repeats", type=int, default=20)
parser.add_argument("--users", type=int, default=5)
parser.add_argument("--external-env", action="store_true", help="Use existing GMV_DATABASE_URL instead of the Windows helper")
parser.add_argument("--seed-only", action="store_true")
parser.add_argument("--skip-seed", action="store_true")
parser.add_argument("--http-url", help="Measure a running HTTP service instead of in-process ASGI")
parser.add_argument("--hardware-note", default="Actual host; CPU and RAM were not capped to the planned 4-core/16-GB baseline.")
parser.add_argument("--report", type=Path, default=Path("docs/benchmark-results.json"))
args = parser.parse_args()
if not args.external_env:
    environment("genmedia_bench")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text, select  # noqa: E402
from genmedia.db import engine, session_factory  # noqa: E402
from genmedia.models import Library, User  # noqa: E402
from genmedia.security import hash_password  # noqa: E402

def seed_database():
    if not engine().url.database.endswith("_bench"):
        raise RuntimeError("Benchmark seeding requires a dedicated *_bench database")
    command.upgrade(Config("alembic.ini"), "head")
    with engine().begin() as connection:
        from genmedia.db import Base
        connection.execute(text("TRUNCATE " + ",".join('"' + t + '"' for t in Base.metadata.tables) + " CASCADE"))
    with session_factory()() as db:
        library = Library(name="Synthetic benchmark", mode="managed")
        db.add(library)
        for i in range(args.users):
            db.add(User(username=f"bench{i}", password_hash=hash_password("benchmark-password"), role="user"))
        db.commit()
        library_id = library.id
        users = list(db.scalars(select(User.id).order_by(User.username)))

    with engine().begin() as connection:
        connection.execute(text("""
            INSERT INTO assets (id, created_at, library_id, sha256, filename, extension, mime_type,
              width, height, file_size, imported_at, source_created_at, status, warnings)
            SELECT md5(i::text)::uuid, now(), CAST(:library AS uuid), lpad(md5(i::text),64,'0'),
              'image-' || i || '.png', '.png', 'image/png', 1024, 1536, 2000000+i,
              now()-i*interval '1 second', now()-i*interval '1 second', 'ready', '[]'::jsonb
            FROM generate_series(1, :count) AS i
        """), {"library": library_id, "count": args.assets})
        connection.execute(text("""
            INSERT INTO generations (asset_id, generator, model, prompt, negative, seed, steps, cfg, sampler,
              scheduler, normalized, conflicts, parsed_at)
            SELECT md5(i::text)::uuid,
              CASE WHEN i%3=0 THEN 'novelai' WHEN i%3=1 THEN 'a1111' ELSE 'comfyui' END,
              'model-'||(i%20),
              CASE WHEN i%2=0 THEN 'white hair, blue eyes, cinematic lighting, masterpiece, outdoor, 眼'
                   ELSE 'black hair, green eyes, soft lighting, portrait, indoor' END || ', unique-' || i,
              'bad hands, low quality', i::text, 20+i%20, 4+(i%8)*0.5, 'euler', 'normal',
              '{}'::jsonb, '[]'::jsonb, now()
            FROM generate_series(1, :count) AS i
        """), {"count": args.assets})
        connection.execute(text("""
            INSERT INTO prompt_tokens (id, created_at, asset_id, token, polarity, weight, position, category)
            SELECT md5('token-'||i)::uuid, now(), md5(i::text)::uuid,
              CASE WHEN i%2=0 THEN 'white hair' ELSE 'black hair' END, 'positive', 1, 0, 'hair'
            FROM generate_series(1, :count) AS i
        """), {"count": args.assets})
        for user_id in users:
            connection.execute(text("""INSERT INTO user_assets (user_id, asset_id, favorite, rating, review, notes)
                SELECT CAST(:user AS uuid), md5(i::text)::uuid, i%4=0, 1+(i/5)%5, 'unreviewed', ''
                FROM generate_series(1,:count,5) AS i"""), {"user": user_id, "count": args.assets})
        connection.execute(text("ANALYZE"))

if not args.skip_seed:
    seed_database()
if args.seed_only:
    raise SystemExit(0)

queries = [
    ("structured", "generator:novelai AND steps:>=28"),
    ("personal", "rating:>=4 AND NOT favorite:true"),
    ("prompt_combination", 'prompt:"white hair" AND (model:model-2 OR model:model-4) AND cfg:4..7'),
    ("prompt_token", 'prompt.tag:"white hair" AND NOT negative:"monochrome"'),
    ("short_keyword", 'prompt:"眼"'),
]


def run_user(index):
    timings = {name: [] for name, _ in queries}
    matches = {}
    if args.http_url:
        from httpx import Client
        transport = Client(base_url=args.http_url, timeout=30)
    else:
        from fastapi.testclient import TestClient
        from genmedia.main import app
        transport = TestClient(app)
    with transport as client:
        response = client.post("/api/v1/auth/login", json={"username": f"bench{index}", "password": "benchmark-password"})
        client.headers["X-CSRF-Token"] = response.json()["csrf"]
        for name, query in queries:
            response = client.post("/api/v1/search", json={"query": query, "facets": True})
            assert response.status_code == 200, response.text
            matches[name] = response.json()["facets"]["total"]
            assert matches[name] > 0, f"Benchmark query must match assets: {name}"
        for _ in range(args.repeats):
            for name, query in queries:
                start = time.perf_counter()
                response = client.post("/api/v1/search", json={"query": query, "facets": True})
                elapsed = (time.perf_counter() - start) * 1000
                assert response.status_code == 200, response.text
                timings[name].append(elapsed)
    return {"timings": timings, "matches": matches}


print(f"Benchmarking {args.assets} assets with {args.users} concurrent authenticated users", flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=args.users) as pool:
    batches = list(pool.map(run_user, range(args.users)))
output = {"assets": args.assets, "users": args.users, "platform": platform.platform(),
          "load_generator_logical_cpus": os.cpu_count(), "transport": args.http_url or "FastAPI TestClient (HTTP/ASGI in-process)",
          "includes": "validation, authentication, SQL, first 60 results, total count, generator facets, JSON serialization",
          "excludes": "browser, original files, disk import, thumbnail generation" + ("" if args.http_url else ", network"),
          "hardware_note": args.hardware_note,
          "queries": {}}
for name, query in queries:
    values = sorted(value for batch in batches for value in batch["timings"][name])
    p95 = values[min(len(values)-1, int(len(values)*0.95))]
    target = 1000 if name in ("structured", "personal") else 2000
    output["queries"][name] = {"query": query, "samples": len(values), "matched_assets_per_user": [batch["matches"][name] for batch in batches], "median_ms": round(statistics.median(values), 2),
                                "p95_ms": round(p95, 2), "target_ms": target, "passed": p95 <= target}
args.report.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(output["queries"], indent=2), flush=True)
raise SystemExit(0 if all(result["passed"] for result in output["queries"].values()) else 1)
