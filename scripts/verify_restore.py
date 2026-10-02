"""Restore the isolated dev database into a dedicated empty test database and compare evidence."""
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import psycopg
from dev_database import CONFIG, DATA

config = json.loads(CONFIG.read_text(encoding="utf-8"))
binary = Path(os.environ.get("GMV_PG_CLIENT_BIN", str(Path(config["runtime"]) / "clients")))
if not (binary / "pg_dump.exe").exists():
    raise RuntimeError("请将 GMV_PG_CLIENT_BIN 设置为含 PostgreSQL 17 pg_dump.exe / pg_restore.exe 的目录")
connection = {"host": "127.0.0.1", "port": 55432, "user": "genmedia", "password": config["password"]}
backup = DATA / "restore-check.dump"
env = {**os.environ, "PGPASSWORD": config["password"]}
args = ["-h", "127.0.0.1", "-p", "55432", "-U", "genmedia"]
with backup.open("wb") as output:
    subprocess.run([str(binary / "pg_dump.exe"), *args, "-d", "genmedia_dev", "-Fc"], env=env, stdout=output, check=True)
with psycopg.connect(**connection, dbname="genmedia_restore_test", autocommit=True) as restored:
    if restored.execute("SELECT current_database()").fetchone()[0] != "genmedia_restore_test":
        raise RuntimeError("Refusing to modify an unrelated database")
    restored.execute("DROP SCHEMA public CASCADE")
    restored.execute("CREATE SCHEMA public")
subprocess.run([str(binary / "pg_restore.exe"), *args, "-d", "genmedia_restore_test", "--no-owner", "--exit-on-error", str(backup)], env=env, check=True)
checks = {}
media = DATA / "genmedia_dev"
media_backup = DATA / "restore-media.tar"
with tarfile.open(media_backup, "w") as archive:
    archive.add(media, arcname=".")
restored_media = Path(tempfile.mkdtemp(prefix="restored-media-", dir=DATA))
with tarfile.open(media_backup) as archive:
    for member in archive:
        relative = PurePosixPath(member.name)
        if relative.is_absolute() or ".." in relative.parts or not (member.isfile() or member.isdir()):
            raise RuntimeError("Unexpected unsafe entry in test media archive")
        target = restored_media.joinpath(*relative.parts)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
with psycopg.connect(**connection, dbname="genmedia_dev") as original, psycopg.connect(**connection, dbname="genmedia_restore_test") as restored:
    tables = original.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
    for (table,) in tables:
        from psycopg import sql
        query = sql.SQL("SELECT row_to_json(t)::text FROM {} t ORDER BY row_to_json(t)::text").format(sql.Identifier(table))
        before, after = original.execute(query).fetchall(), restored.execute(query).fetchall()
        assert before == after, table
        checks[table] = {"rows": len(before), "identical": True}
    media_count = 0
    for path, checksum in restored.execute("SELECT f.path,a.sha256 FROM physical_files f JOIN assets a ON a.id=f.asset_id JOIN libraries l ON l.id=a.library_id WHERE f.role='original' AND l.mode='managed'"):
        recovered_path = restored_media / Path(path).relative_to(media)
        digest = hashlib.sha256(recovered_path.read_bytes()).hexdigest()
        assert digest == checksum
        media_count += 1
output = {"verified_at": datetime.now(timezone.utc).isoformat(), "postgresql_restore": "passed",
          "tables": checks, "restored_original_hashes_verified": media_count,
          "scope": "pg_dump/pg_restore on PostgreSQL 17 and media tar extraction to a separate directory; Docker volume orchestration not exercised"}
Path("docs/restore-verification.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
print(json.dumps({"restore": "passed", "tables": len(checks), "originals": media_count}))
