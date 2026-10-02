"""Optional isolated Windows test cluster, stored entirely under ignored data/.

Install the PostgreSQL 17 binaries first (see docs/DEVELOPMENT.md).
This helper never changes a system PostgreSQL installation.
"""
import argparse
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BINARY = DATA / "test-runtime/node_modules/@embedded-postgres/windows-x64/native/bin"
CLUSTER = DATA / "postgres-test"
CONFIG = DATA / "test-environment.json"


def environment(database="genmedia_test"):
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    os.environ["GMV_DATABASE_URL"] = f"postgresql+psycopg://genmedia:{config['password']}@127.0.0.1:55432/{database}"
    os.environ["GMV_DATA_DIR"] = str(DATA / database)
    os.environ["GMV_IMPORT_ROOTS"] = json.dumps([str(DATA / "test-imports")])
    os.environ["GMV_ADMIN_PASSWORD"] = config["admin_password"]
    os.environ["PYTHONUTF8"] = "1"
    os.environ["GMV_TEST_DATABASE_URL"] = os.environ["GMV_DATABASE_URL"]
    dump = Path(config.get("runtime", DATA)) / "clients/pg_dump.exe"
    if dump.is_file():
        os.environ["GMV_PG_DUMP_BINARY"] = str(dump)


def start():
    DATA.mkdir(exist_ok=True)
    if not CONFIG.exists():
        CONFIG.write_text(json.dumps({"password": secrets.token_urlsafe(24),
                                      "admin_password": secrets.token_urlsafe(18)}), encoding="utf-8")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    # PostgreSQL's Windows bootstrap embeds its binary path in SQL using the system code page.
    # Use an ASCII-only, task-specific temporary runtime when the checkout has non-ASCII characters.
    runtime = DATA
    if not str(ROOT).isascii():
        import hashlib
        runtime = Path(tempfile.gettempdir()) / ("genmedia-pg-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:12])
        runtime.mkdir(exist_ok=True)
        if not (runtime / "native/bin/initdb.exe").exists():
            shutil.copytree(BINARY.parent, runtime / "native", dirs_exist_ok=True)
    binary = runtime / "native/bin" if runtime != DATA else BINARY
    cluster = runtime / "postgres-test"
    config["runtime"] = str(runtime)
    CONFIG.write_text(json.dumps(config), encoding="utf-8")
    if not (cluster / "PG_VERSION").exists():
        password_file = runtime / "pg-init-password"
        password_file.write_text(config["password"], encoding="utf-8")
        try:
            subprocess.run([str(binary / "initdb.exe"), "-D", str(cluster), "-U", "genmedia",
                            "-A", "scram-sha-256", f"--pwfile={password_file}", "--encoding=UTF8", "--locale=C"],
                           check=True)
        finally:
            password_file.unlink(missing_ok=True)
    try:
        connection = psycopg.connect(host="127.0.0.1", port=55432, user="genmedia", password=config["password"],
                                     dbname="postgres", connect_timeout=2, autocommit=True)
    except psycopg.OperationalError:
        with (DATA / "postgres-test.log").open("ab") as logfile:
            subprocess.Popen([str(binary / "postgres.exe"), "-D", str(cluster), "-h", "127.0.0.1", "-p", "55432"],
                             stdin=subprocess.DEVNULL, stdout=logfile, stderr=logfile,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        for _ in range(30):
            try:
                connection = psycopg.connect(host="127.0.0.1", port=55432, user="genmedia", password=config["password"],
                                             dbname="postgres", connect_timeout=1, autocommit=True)
                break
            except psycopg.OperationalError:
                time.sleep(1)
        else:
            raise RuntimeError("PostgreSQL 未启动，参见 data/postgres-test.log")
    with connection:
        for database in ("genmedia_test", "genmedia_dev", "genmedia_bench", "genmedia_restore_test"):
            if not connection.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database,)).fetchone():
                connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    (DATA / "test-imports").mkdir(exist_ok=True)
    print("Isolated PostgreSQL 17 ready on 127.0.0.1:55432")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop"])
    args = parser.parse_args()
    if args.action == "start":
        start()
    else:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        runtime = Path(config.get("runtime", DATA))
        binary = runtime / "native/bin" if runtime != DATA else BINARY
        subprocess.run([str(binary / "pg_ctl.exe"), "-D", str(runtime / "postgres-test"), "stop", "-m", "fast"],
                       check=True, creationflags=subprocess.CREATE_NO_WINDOW)
