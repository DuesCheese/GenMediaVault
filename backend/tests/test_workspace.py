import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from genmedia.config import settings
from genmedia.maintenance import mutation_gate
from genmedia.snapshots import restore_snapshot
from genmedia.worker import run_one
from genmedia.workspace import segments
from test_integration import image_bytes, upload


def test_prompt_segments_preserve_weights_and_commas():
    assert segments(r'white hair, 1.5::blue eyes, smile::, (sky, clouds:1.2), red\,blue') == [
        'white hair', '1.5::blue eyes, smile::', '(sky, clouds:1.2)', r'red\,blue']


@pytest.mark.integration
def test_shared_prompt_groups_revision_and_reparse(clients):
    alice, bob, admin = (clients[name] for name in ('alice', 'bob', 'admin'))
    id = upload(alice)
    url = f'/api/v1/assets/{id}/prompt-groups'
    layout = alice.get(url).json()
    group = {'id': str(uuid4()), 'name': '外观', 'color': '#8fbcbb', 'source': 'base:positive',
             'tokens': layout['sources'][0]['tokens'][:1]}
    assert alice.put(url, json={'revision': 0, 'groups': [group]}).status_code == 200
    assert bob.get(url).json()['groups'] == [group]
    assert bob.put(url, json={'revision': 0, 'groups': []}).status_code == 409
    assert alice.put(url, json={'revision': 1, 'groups': [{**group, 'color': 'red'}]}).status_code == 422
    assert alice.put(url, json={'revision': 1, 'groups': [group, {**group, 'id': str(uuid4())}]}).status_code == 422
    admin.post('/api/v1/assets/bulk', json={'asset_ids': [id], 'action': 'reparse'})
    assert run_one()
    assert alice.get(url).json()['groups'] == [group]


@pytest.mark.integration
def test_reference_export_permissions_and_tag_deletion(clients):
    alice, bob, admin = (clients[name] for name in ('alice', 'bob', 'admin'))
    id = upload(alice)
    url = f'/api/v1/assets/{id}/attachments'
    assert alice.post(url, files={'file': ('bad.png', b'not an image')}).status_code == 422
    response = alice.post(url, data={'caption': '手势', 'character_index': 0},
                          files={'file': ('动作.png', image_bytes(), 'image/png')})
    assert response.status_code == 201, response.text
    reference = response.json()
    assert bob.get(reference['url']).content == image_bytes()
    assert bob.get(url).json()[0]['caption'] == '手势'
    for metadata in (False, True):
        job = alice.post('/api/v1/exports', json={'asset_ids': [id], 'include_metadata': metadata}).json()['job_id']
        assert run_one()
        assert bob.get(f'/api/v1/jobs/{job}').status_code == 403
        assert bob.get(f'/api/v1/jobs/{job}/download').status_code == 403
        archive = zipfile.ZipFile(io.BytesIO(alice.get(f'/api/v1/jobs/{job}/download').content))
        assert any(name.endswith('测试图片.png') for name in archive.namelist())
        assert any(name.startswith(f'references/{id}/') for name in archive.namelist())
        assert bool([name for name in archive.namelist() if name.endswith('.png.json')]) == metadata
    tag = next(t for t in admin.get('/api/v1/tags').json() if t['name'] == 'hair:white')
    assert alice.delete(f"/api/v1/tags/{tag['id']}").status_code == 403
    assert admin.delete(f"/api/v1/tags/{tag['id']}").status_code == 200
    admin.post('/api/v1/rules/apply')
    assert run_one()
    assert 'hair:white' not in alice.get(f'/api/v1/assets/{id}').json()['tags']
    assert 'hair:white' not in [t['name'] for t in alice.get('/api/v1/tags').json()]
    alice.post('/api/v1/assets/bulk', json={'asset_ids': [id], 'action': 'add_tag', 'value': 'hair:white'})
    assert 'hair:white' in alice.get(f'/api/v1/assets/{id}').json()['tags']
    alice.post('/api/v1/auth/logout')
    assert alice.get(reference['url']).status_code == 401
    assert bob.delete(f"/api/v1/attachments/{reference['id']}").status_code == 200
    assert bob.get(reference['url']).status_code == 404


@pytest.mark.integration
def test_backup_read_only_gate_and_admin_permissions(clients):
    alice, admin = clients['alice'], clients['admin']
    id = upload(alice)
    assert alice.post('/api/v1/backups', json={}).status_code == 403
    assert alice.get('/api/v1/backups').status_code == 403
    job = admin.post('/api/v1/backups', json={'label': '权限检查'}).json()['job_id']
    assert alice.get(f'/api/v1/jobs/{job}').status_code == 403
    assert alice.post(f'/api/v1/jobs/{job}/cancel').status_code == 403
    assert job not in [item['id'] for item in alice.get('/api/v1/jobs').json()]
    with mutation_gate(lambda: None, exclusive=True):
        assert alice.patch(f'/api/v1/assets/{id}/personal', json={'rating': 3}).status_code == 503
        assert alice.get(f'/api/v1/assets/{id}/original').status_code == 200
        assert alice.post('/api/v1/search', json={}).status_code == 200
        assert admin.post(f'/api/v1/jobs/{job}/cancel').status_code == 200
    assert alice.patch(f'/api/v1/assets/{id}/personal', json={'rating': 3}).status_code == 200


@pytest.mark.integration
def test_full_snapshot_and_restore_to_empty_database(clients, postgres, tmp_path, monkeypatch):
    if not shutil.which(settings().pg_dump_binary):
        pytest.skip('PostgreSQL 17 pg_dump/pg_restore required')
    admin, alice = clients['admin'], clients['alice']
    id = upload(alice)
    alice.patch(f'/api/v1/assets/{id}/personal', json={'notes': '恢复后保留的私人备注', 'rating': 5})
    ref = alice.post(f'/api/v1/assets/{id}/attachments', files={'file': ('pose.png', image_bytes())}).json()
    source_root = settings().import_roots[0] / str(uuid4())
    source_root.mkdir()
    (source_root / '角色.png').write_bytes(image_bytes(seed='42'))
    (source_root / '角色.png.json').write_text(json.dumps({'generation': {'seed': '42'}, 'schema_version': 1}))
    library = admin.post('/api/v1/libraries', json={'name': '恢复索引库', 'mode': 'indexed', 'root_path': str(source_root)}).json()['id']
    assert run_one()
    job = admin.post('/api/v1/backups', json={'label': '完整快照', 'include_indexed': True}).json()['job_id']
    assert run_one()
    state = admin.get(f'/api/v1/jobs/{job}').json()
    assert state['status'] == 'completed', state
    assert alice.get(f'/api/v1/jobs/{job}/download').status_code == 403
    backup = tmp_path / 'snapshot.zip'
    backup.write_bytes(admin.get(f'/api/v1/jobs/{job}/download').content)
    with zipfile.ZipFile(backup) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['migration'] == '0002'
        assert manifest['label'] == '完整快照'
        assert any(entry['path'].endswith('角色.png.json') for entry in manifest['files'])
        assert not any('backups/' in entry['path'] for entry in manifest['files'])
        for entry in manifest['files']:
            assert hashlib.sha256(archive.read(entry['path'])).hexdigest() == entry['sha256']
    # The test owns this unique database; never restore over the live or shared test database.
    database_name = f'snapshot_{uuid4().hex}_test'
    with postgres.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
        connection.execute(text(f'CREATE DATABASE {database_name}'))
    restored = create_engine(postgres.url.set(database=database_name))
    target = tmp_path / 'restored'
    monkeypatch.setattr(settings(), 'data_dir', target)
    settings().prepare()
    monkeypatch.setattr('genmedia.snapshots.engine', lambda: restored)
    try:
        restore_snapshot(backup)
        with restored.connect() as connection:
            assert connection.scalar(text('SELECT count(*) FROM assets')) == 2
            assert connection.scalar(text('SELECT count(*) FROM sessions')) == 0
            assert connection.scalar(text('SELECT notes FROM user_assets WHERE rating=5')) == '恢复后保留的私人备注'
            assert connection.scalar(text('SELECT count(*) FROM attachments')) == 1
            files = connection.execute(text('SELECT path FROM physical_files WHERE role=\'original\''))
            assert all(Path(row[0]).is_file() for row in files)
        assert (target / 'restored-indexed' / library / '角色.png').read_bytes() == image_bytes(seed='42')
        assert any((target / 'attachments').iterdir()), ref
        with pytest.raises(ValueError, match='空数据库'):
            restore_snapshot(backup)
    finally:
        restored.dispose()
        with postgres.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
            connection.execute(text(f'DROP DATABASE {database_name}'))
