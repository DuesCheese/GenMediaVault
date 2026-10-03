"""Read-only v0.4 deployment checks against the local application."""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
config = dotenv_values(root / '.env')
with httpx.Client(base_url='http://127.0.0.1:8080', timeout=30) as client:
    assert client.get('/api/v1/tag-groups').status_code == 401
    response = client.post('/api/v1/auth/login', json={'username': config.get('GMV_ADMIN_USERNAME', 'admin'),
                                                     'password': config['GMV_ADMIN_PASSWORD']})
    response.raise_for_status()
    client.headers['X-CSRF-Token'] = response.json()['csrf']
    try:
        assert client.get('/openapi.json').json()['info']['version'] == '0.4.0'
        groups = client.get('/api/v1/tag-groups')
        groups.raise_for_status()
        dictionary = client.get('/api/v1/translations')
        dictionary.raise_for_status()
        response = client.post('/api/v1/search', json={'limit': 5})
        response.raise_for_status()
        checked = 0
        for asset in response.json()['items']:
            layout = client.get(f"/api/v1/assets/{asset['id']}/prompt-groups")
            layout.raise_for_status()
            assert isinstance(layout.json()['catalog_revision'], int)
            checked += 1
    finally:
        client.post('/api/v1/auth/logout').raise_for_status()
    assert client.get('/api/v1/tag-groups').status_code == 401
revision = subprocess.run(['docker', 'compose', 'exec', '-T', 'postgres', 'psql', '-U', 'genmedia',
                           '-d', 'genmedia', '-Atc', 'SELECT version_num FROM alembic_version'],
                          capture_output=True, text=True, check=True).stdout.strip()
assert revision == '0004'
report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'version': '0.4.0', 'migration': revision,
          'global_groups': len(groups.json()), 'translations': dictionary.json()['total'],
          'existing_image_layouts_checked': checked, 'login_and_logout_protection': True}
(root / 'docs/v04-deployment-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
