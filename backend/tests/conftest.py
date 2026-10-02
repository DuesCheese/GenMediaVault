import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url


@pytest.fixture(scope="session")
def postgres(tmp_path_factory):
    url = os.environ.get("GMV_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set GMV_TEST_DATABASE_URL to a dedicated PostgreSQL *_test database")
    if not (make_url(url).database or "").endswith("_test"):
        raise RuntimeError("Integration tests require an explicitly named *_test database")
    os.environ["GMV_DATABASE_URL"] = url
    from genmedia.config import settings
    from genmedia.db import engine
    settings.cache_clear()
    engine.cache_clear()
    config = settings()
    config.data_dir = tmp_path_factory.mktemp("media")
    config.import_roots = [tmp_path_factory.mktemp("source")]
    config.stability_seconds = 0
    config.prepare()
    command.upgrade(Config("alembic.ini"), "head")
    yield engine()
    engine().dispose()


@pytest.fixture
def database(postgres):
    from genmedia.db import Base, session_factory
    from genmedia.models import User
    from genmedia.security import bootstrap, hash_password
    with postgres.begin() as connection:
        tables = ", ".join('"' + table + '"' for table in Base.metadata.tables)
        connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    with session_factory()() as db:
        bootstrap(db, "admin", "test-admin-password")
        for username in ("alice", "bob"):
            db.add(User(username=username, password_hash=hash_password("test-user-password"), role="user"))
        db.commit()
    return session_factory()


@pytest.fixture
def clients(database):
    from fastapi.testclient import TestClient
    from genmedia.main import app
    result = {}
    for username in ("admin", "alice", "bob"):
        client = TestClient(app)
        response = client.post("/api/v1/auth/login", json={"username": username,
                               "password": "test-admin-password" if username == "admin" else "test-user-password"})
        assert response.status_code == 200, response.text
        client.headers["X-CSRF-Token"] = response.json()["csrf"]
        result[username] = client
    yield result
    for client in result.values():
        client.close()
