"""Read-only release checks against the local deployed dictionary; never print credentials."""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
configuration = dotenv_values(root / '.env')
with httpx.Client(base_url='http://127.0.0.1:8080', timeout=30) as client:
    assert client.get('/api/v1/translations').status_code == 401
    login = client.post('/api/v1/auth/login', json={
        'username': configuration.get('GMV_ADMIN_USERNAME', 'admin'),
        'password': configuration['GMV_ADMIN_PASSWORD']})
    login.raise_for_status()
    client.headers['X-CSRF-Token'] = login.json()['csrf']
    try:
        assert client.get('/openapi.json').json()['info']['version'] == '0.3.0'
        response = client.get('/api/v1/translations')
        response.raise_for_status()
        count = response.json()['total']
        groups = client.get('/api/v1/translations/groups').json()
        assert count == 11073 and len(groups) == 241
        for query in ('白发', 'WHITE_H'):
            response = client.get('/api/v1/translations', params={'q': query})
            response.raise_for_status()
            assert any(row['tag'] == 'white hair' for row in response.json()['items'])
        response = client.post('/api/v1/translations/lookup', json={
            'texts': ['white hair', '(blue eyes:1.2)', '{{masterpiece}}', 'unknown demo phrase']})
        response.raise_for_status()
        items = response.json()['items']
        assert all(item['matched'] for item in items[:3])
        assert items[1]['translated'].startswith('(') and items[1]['translated'].endswith(':1.2)')
        assert items[2]['translated'].startswith('{{') and items[2]['translated'].endswith('}}')
        assert not items[3]['matched'] and items[3]['translated'] == items[3]['text']
    finally:
        client.post('/api/v1/auth/logout').raise_for_status()
    assert client.get('/api/v1/translations').status_code == 401

revision = subprocess.run(['docker', 'compose', 'exec', '-T', 'postgres', 'psql', '-U', 'genmedia',
                           '-d', 'genmedia', '-Atc', 'SELECT version_num FROM alembic_version'],
                          capture_output=True, text=True, check=True).stdout.strip()
assert revision == '0003'
report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'version': '0.3.0',
          'migration': revision, 'translations': count, 'groups': len(groups),
          'chinese_and_english_search': True, 'weighted_translation_and_unknown_fallback': True,
          'login_and_logout_protection': True}
(root / 'docs/v03-deployment-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
