from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from genmedia.main import app
from genmedia.models import ShareLink, User, now
from genmedia.worker import run_one
from test_integration import image_bytes, upload

pytestmark = pytest.mark.integration


def bulk(client, ids, action, value=None):
    return client.post('/api/v1/assets/bulk', json={'asset_ids': ids, 'action': action, 'value': value})


def search(client, **body):
    response = client.post('/api/v1/search', json=body)
    assert response.status_code == 200, response.text
    return response.json()['items']


def test_private_access_and_publication_all_entry_points(clients):
    alice, bob, admin = (clients[n] for n in ('alice', 'bob', 'admin'))
    id = upload(alice)
    ref = alice.post(f'/api/v1/assets/{id}/attachments', files={'file': ('pose.png', image_bytes())}).json()
    detail = alice.get(f'/api/v1/assets/{id}').json()
    assert detail['uploader'] == 'alice' and not detail['is_public']
    assert admin.get('/api/v1/auth/me').json()['user']['role'] == 'superadmin'
    assert search(bob, facets=True) == []
    assert bob.get('/api/v1/libraries').json()[0]['count'] == 0
    assert bob.get('/api/v1/search/suggest?q=portrait').json() == []
    assert bob.get('/api/v1/tags').json() == []
    for suffix in ('', '/original', '/thumbnail', '/workflow', '/prompt-groups', '/attachments'):
        assert bob.get(f'/api/v1/assets/{id}{suffix}').status_code == 404
    assert bob.get(ref['url']).status_code == 404
    assert bob.delete(f"/api/v1/attachments/{ref['id']}").status_code == 404
    assert bob.patch(f'/api/v1/assets/{id}/personal', json={'rating': 1}).status_code == 404
    assert bob.put(f'/api/v1/assets/{id}/prompt-groups', json={'revision': 0, 'groups': []}).status_code == 404
    for action in ('export', 'add_tag', 'publish', 'unpublish'):
        assert bulk(bob, [id], action, 'test').status_code == 404
    assert bob.post('/api/v1/exports', json={'asset_ids': [id]}).status_code == 404
    assert len(search(admin, space='all_private')) == 1
    assert bob.post('/api/v1/search', json={'space': 'all_private'}).status_code == 403
    assert bulk(alice, [id], 'publish').status_code == 202
    assert search(bob, space='public')[0]['id'] == id
    assert search(alice, space='private')[0]['is_public']
    assert bob.get(ref['url']).status_code == 200
    assert bulk(bob, [id], 'unpublish').status_code == 403
    exported = bob.post('/api/v1/exports', json={'asset_ids': [id]}).json()['job_id']
    assert run_one()
    assert bob.get(f'/api/v1/jobs/{exported}/download').status_code == 200
    assert bulk(alice, [id], 'unpublish').status_code == 202
    assert bob.get(f'/api/v1/jobs/{exported}/download').status_code == 403
    assert search(bob) == []


def test_per_owner_dedup_and_transfer(clients):
    alice, bob, admin = (clients[n] for n in ('alice', 'bob', 'admin'))
    a = upload(alice)
    assert upload(alice) == a
    b = upload(bob)
    assert a != b
    assert len(search(admin)) == 2
    owner = bob.get('/api/v1/auth/me').json()['user']['id']
    assert bulk(alice, [a], 'uploader', owner).status_code == 403
    # Ownership transfer must not silently merge somebody else's curation.
    assert bulk(admin, [a], 'uploader', owner).status_code == 409
    unique = upload(alice, seed='987')
    link = alice.post(f'/api/v1/assets/{unique}/shares', json={'days': 1}).json()['path']
    assert bulk(admin, [unique], 'uploader', owner).status_code == 202
    assert alice.get(f'/api/v1/assets/{unique}').status_code == 404
    assert bob.get(f'/api/v1/assets/{unique}').json()['uploader'] == 'bob'
    assert TestClient(app).get('/api/v1/shared/' + link.split('/')[-1]).status_code == 404


@pytest.mark.parametrize('days', [1, 7, 30])
def test_read_only_share_expiry_and_revoke(clients, database, days):
    alice, bob = clients['alice'], clients['bob']
    id = upload(alice)
    other = upload(bob, seed='77')
    other_ref = bob.post(f'/api/v1/assets/{other}/attachments', files={'file': ('other.png', image_bytes())}).json()
    ref = alice.post(f'/api/v1/assets/{id}/attachments', files={'file': ('pose.png', image_bytes())}).json()
    alice.patch(f'/api/v1/assets/{id}/personal', json={'notes': 'secret', 'rating': 5})
    assert bob.post(f'/api/v1/assets/{id}/shares', json={'days': days}).status_code == 404
    assert alice.post(f'/api/v1/assets/{id}/shares', json={'days': 2}).status_code == 422
    link = alice.post(f'/api/v1/assets/{id}/shares', json={'days': days}).json()
    url = '/api/v1/shared/' + link['path'].split('/')[-1]
    with TestClient(app) as guest:
        value = guest.get(url).json()
        assert value['generation']['seed'] == '18446744073709551615'
        assert not {'notes', 'rating', 'favorite', 'files'} & value.keys()
        assert 'source' not in value['raw'][0] and 'origin' not in value['raw'][0]['data']
        assert guest.get(value['original']).content == image_bytes()
        assert guest.get(value['thumbnail']).status_code == 200
        assert guest.get(value['attachments'][0]['url']).content == image_bytes()
        assert guest.get(url + '/attachments/' + other_ref['id']).status_code == 404
        assert guest.get(ref['url']).status_code == 401
        assert guest.patch(f'/api/v1/assets/{id}/personal', json={'rating': 1}).status_code == 401
        assert guest.post(url, json={'rating': 1}).status_code in (404, 405)
        assert guest.post('/api/v1/assets/bulk', json={'asset_ids': [id], 'action': 'publish'}).status_code == 401
        with database() as db:
            saved = db.get(ShareLink, link['id'])
            assert days * 86400 - 30 <= (saved.expires_at - now()).total_seconds() <= days * 86400
            saved.expires_at = now() - timedelta(seconds=1)
            db.commit()
        assert guest.get(url).status_code == 404
        assert guest.get(value['original']).status_code == 404
        assert guest.get(value['attachments'][0]['url']).status_code == 404
        fresh = alice.post(f'/api/v1/assets/{id}/shares', json={'days': days}).json()
        assert alice.delete(f"/api/v1/assets/{id}/shares/{fresh['id']}").status_code == 200
        assert guest.get('/api/v1/shared/' + fresh['path'].split('/')[-1]).status_code == 404


def test_regular_admin_is_not_superadmin(clients, database):
    admin, alice, bob = (clients[n] for n in ('admin', 'alice', 'bob'))
    with database() as db:
        user = db.scalar(select(User).where(User.username == 'bob'))
        user.role = 'admin'
        db.commit()
    id = upload(alice)
    assert search(bob) == []
    assert bob.get(f'/api/v1/assets/{id}').status_code == 404
    assert bob.get('/api/v1/jobs').json() == []
    super_id = admin.get('/api/v1/auth/me').json()['user']['id']
    for body in ({'password': 'a-new-password'}, {'role': 'admin'}, {'active': False}):
        assert bob.patch(f'/api/v1/users/{super_id}', json=body).status_code == 403
    assert admin.patch(f'/api/v1/users/{super_id}', json={'role': 'admin'}).status_code == 403
    link = alice.post(f'/api/v1/assets/{id}/shares', json={'days': 7}).json()
    assert bulk(alice, [id], 'unpublish').status_code == 202
    assert TestClient(app).get('/api/v1/shared/' + link['path'].split('/')[-1]).status_code == 404


def test_indexed_rescan_preserves_transferred_uploader(clients):
    from uuid import uuid4
    from genmedia.config import settings
    admin, bob = clients['admin'], clients['bob']
    directory = settings().import_roots[0] / str(uuid4())
    directory.mkdir()
    path = directory / 'indexed.png'
    path.write_bytes(image_bytes())
    library = admin.post('/api/v1/libraries', json={'name': 'indexed-owner', 'mode': 'indexed', 'root_path': str(directory)}).json()['id']
    assert run_one()
    asset = search(admin, library_id=library)[0]
    owner = bob.get('/api/v1/auth/me').json()['user']['id']
    assert bulk(admin, [asset['id']], 'uploader', owner).status_code == 202
    path.write_bytes(image_bytes())  # same content, new file status: triggers a real rescan
    assert admin.post(f'/api/v1/libraries/{library}/scan').status_code == 202
    assert run_one()
    items = search(admin, library_id=library)
    assert len(items) == 1 and items[0]['uploader_id'] == owner
    assert bob.get(f"/api/v1/assets/{asset['id']}/original").content == image_bytes()


def test_update_permissions_and_persistent_request(clients, tmp_path, monkeypatch):
    import time
    import json
    from genmedia import updates
    from genmedia.config import settings
    monkeypatch.setattr(settings(), 'update_control_dir', str(tmp_path))
    (tmp_path / 'heartbeat.json').write_text(json.dumps({'time': time.time()}))
    monkeypatch.setattr(updates, 'latest_release', lambda: {'version': '0.7.0', 'tag': 'v0.7.0', 'files': {}, 'url': 'https://github.com/DuesCheese/GenMediaVault/releases/tag/v0.7.0', 'notes': 'test'})
    alice, admin = clients['alice'], clients['admin']
    for path in ('/api/v1/system/update', '/api/v1/system/update/check'):
        assert alice.get(path).status_code == 403
    assert alice.post('/api/v1/system/update').status_code == 403
    assert admin.get('/api/v1/system/update/check').json()['available']
    assert admin.post('/api/v1/system/update').status_code == 202
    assert admin.post('/api/v1/system/update').status_code == 409
    request = json.loads((tmp_path / 'request.json').read_text())
    assert request['current'] == '0.6.1'


def test_update_read_only_window_blocks_jobs_and_writes(clients, tmp_path, monkeypatch):
    import json
    from genmedia.config import settings
    from genmedia.worker import claim
    alice = clients['alice']
    id = upload(alice)
    monkeypatch.setattr(settings(), 'update_control_dir', str(tmp_path))
    (tmp_path / 'status.json').write_text(json.dumps({'phase': 'deploying'}))
    assert alice.patch(f'/api/v1/assets/{id}/personal', json={'rating': 2}).status_code == 503
    assert claim() is None
    assert alice.get(f'/api/v1/assets/{id}').status_code == 200
    assert search(alice)


def test_upgrade_existing_assets_preserves_visibility_and_assigns_admin(postgres, monkeypatch):
    from uuid import uuid4
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from genmedia import db as database_module
    name = f'privacy_{uuid4().hex}_test'
    with postgres.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    target = create_engine(postgres.url.set(database=name))
    try:
        monkeypatch.setattr(database_module, 'engine', lambda: target)
        command.upgrade(Config('alembic.ini'), '0004')
        owner, library, asset = [str(uuid4()) for _ in range(3)]
        with target.begin() as connection:
            connection.execute(text("INSERT INTO users (id, created_at, username, password_hash, role, active) VALUES (:id, now(), 'admin', 'synthetic', 'admin', true)"), {'id': owner})
            connection.execute(text("INSERT INTO libraries (id, created_at, name, mode, watch_enabled) VALUES (:id, now(), 'old', 'managed', false)"), {'id': library})
            connection.execute(text("INSERT INTO assets (id, created_at, library_id, sha256, filename, extension, mime_type, width, height, file_size, imported_at, status, warnings) VALUES (:id, now(), :library, :sha, 'legacy.png', '.png', 'image/png', 32, 32, 100, now(), 'ready', '[]')"), {'id': asset, 'library': library, 'sha': '0' * 64})
        command.upgrade(Config('alembic.ini'), 'head')
        with target.connect() as connection:
            assert connection.execute(text('SELECT uploader_id::text, is_public FROM assets')).one() == (owner, True)
            assert connection.scalar(text("SELECT role FROM users WHERE username='admin'")) == 'superadmin'
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0005'
    finally:
        target.dispose()
        with postgres.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
            connection.execute(text(f'DROP DATABASE "{name}"'))
