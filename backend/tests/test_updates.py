import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from genmedia import releases


def bundle(extra=None):
    files = {'compose.yaml': b'services: {}', 'Dockerfile': b'FROM test', 'pyproject.toml': b'version="0.6.0"', **(extra or {})}
    manifest = {'version': '0.6.0', 'working_tree_dirty': False, 'files': [
        {'path': k, 'size': len(v), 'sha256': hashlib.sha256(v).hexdigest()} for k, v in files.items()]}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('GenMediaVault/RELEASE-MANIFEST.json', json.dumps(manifest))
        for name, content in files.items():
            archive.writestr('GenMediaVault/' + name, content)
    return output.getvalue()


def test_release_bundle_validation():
    assert releases.verified_bundle(bundle(), '0.6.0')['Dockerfile'] == b'FROM test'
    for path in ('../escape', '/absolute', 'data/private.png', '.env', 'C:/escape', 'foo\\bar'):
        with pytest.raises(ValueError):
            releases.verified_bundle(bundle({path: b'bad'}), '0.6.0')
    with pytest.raises(ValueError):
        releases.verified_bundle(bundle(), '0.7.0')
    assert releases.version_tuple('v0.10.0') > releases.version_tuple('0.9.0')


@pytest.mark.parametrize('fail_deployment', [False, True])
def test_controller_preserves_env_mounts_and_rolls_back_database(tmp_path, monkeypatch, fail_deployment):
    monkeypatch.setitem(sys.modules, 'releases', releases)
    spec = importlib.util.spec_from_file_location('updater_test', Path(__file__).parents[2] / 'scripts/updater.py')
    controller = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controller)
    root, control = tmp_path / 'workspace', tmp_path / 'control'
    root.mkdir()
    control.mkdir()
    (root / '.env').write_text('POSTGRES_PASSWORD=must-stay-private')
    (root / 'compose.yaml').write_text('old-compose')
    (root / 'Dockerfile').write_text('old-build')
    monkeypatch.setattr(controller, 'WORKSPACE', root)
    monkeypatch.setattr(controller, 'CONTROL', control)
    monkeypatch.setenv('HOSTNAME', 'updater')
    monkeypatch.setattr(controller, 'latest_release', lambda: {'version': '0.6.0'})
    monkeypatch.setattr(controller, 'release_files', lambda _: {'compose.yaml': b'new-compose', 'Dockerfile': b'new-build'})
    monkeypatch.setattr(controller, 'inspect', lambda id: {'Image': 'old-' + id, 'Config': {'Labels': {'com.docker.compose.project': 'original-project'}}, 'Mounts': [{'Destination': '/imports', 'Source': '/host/Chinese directory/imports'}]})
    calls = []
    def docker(*args, **kwargs):
        calls.append(args)
        if args[0] == 'ps':
            return b'container'
        if args[0] == 'compose':
            assert args[1:3] == ('-p', 'original-project')
            assert kwargs['env']['GMV_IMPORT_ROOT'] == '/host/Chinese directory/imports'
        if fail_deployment and 'up' in args and sum('up' in c for c in calls) == 1:
            raise subprocess.CalledProcessError(1, 'docker')
        return b''
    monkeypatch.setattr(controller, 'docker', docker)
    def dump(args, **kwargs):
        assert 'pg_dump' in args
        kwargs['stdout'].write(b'database-snapshot')
        return subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr(controller.subprocess, 'run', dump)
    request = {'id': '1b304ab9-75bf-445e-a354-d952ac194f28', 'current': '0.5.0'}
    if fail_deployment:
        with pytest.raises(subprocess.CalledProcessError):
            controller.update(request)
        assert (root / 'compose.yaml').read_text() == 'old-compose'
        assert any('pg_restore' in c for c in calls)
        assert sum('up' in c for c in calls) == 2
    else:
        controller.update(request)
        assert (root / 'compose.yaml').read_text() == 'new-compose'
        assert json.loads((control / 'status.json').read_text())['phase'] == 'completed'
    assert (root / '.env').read_text() == 'POSTGRES_PASSWORD=must-stay-private'
    assert (control / 'updates' / request['id'] / 'database.dump').read_bytes() == b'database-snapshot'
    assert next(i for i, c in enumerate(calls) if c[0] == 'build') < next(i for i, c in enumerate(calls) if 'stop' in c)
