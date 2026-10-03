"""Request and verify a full local application snapshot without exposing credentials."""
import argparse
import hashlib
import json
import time
import zipfile
from pathlib import Path

import httpx
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--label', required=True)
args = parser.parse_args()
config = dotenv_values(root / '.env')
with httpx.Client(base_url='http://127.0.0.1:8080', timeout=120) as client:
    response = client.post('/api/v1/auth/login', json={'username': config.get('GMV_ADMIN_USERNAME', 'admin'),
                                                     'password': config['GMV_ADMIN_PASSWORD']})
    response.raise_for_status()
    client.headers['X-CSRF-Token'] = response.json()['csrf']
    try:
        response = client.post('/api/v1/backups', json={'label': args.label, 'include_indexed': True})
        response.raise_for_status()
        job_id = response.json()['job_id']
        for _ in range(300):
            state = client.get(f'/api/v1/jobs/{job_id}')
            state.raise_for_status()
            if state.json()['status'] == 'completed':
                break
            if state.json()['status'] in ('failed', 'cancelled'):
                raise RuntimeError('备份未完成，请在任务页检查原因')
            time.sleep(1)
        else:
            raise TimeoutError('备份任务仍在运行，请在任务页查看进度')
        target = root / 'backups' / f'{job_id}.zip'
        target.parent.mkdir(exist_ok=True)
        with client.stream('GET', f'/api/v1/jobs/{job_id}/download') as response:
            response.raise_for_status()
            with target.open('wb') as output:
                for chunk in response.iter_bytes():
                    output.write(chunk)
        with zipfile.ZipFile(target) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            for entry in manifest['files']:
                digest = hashlib.sha256()
                with archive.open(entry['path']) as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b''):
                        digest.update(chunk)
                assert digest.hexdigest() == entry['sha256'], '归档校验失败'
        print(json.dumps({'path': str(target.relative_to(root)), 'files': len(manifest['files']),
                          'warnings': len(manifest['warnings']), 'migration': manifest['migration'],
                          'verified': True}, ensure_ascii=False))
    finally:
        client.post('/api/v1/auth/logout').raise_for_status()
