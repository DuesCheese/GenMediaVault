"""Compare protected content after restoring the browser-produced v0.2 snapshot."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
configuration = dotenv_values(root / '.env')
results = []
for port in (18083, 18084):
    with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=30) as client:
        login = client.post('/api/v1/auth/login', json={'username': configuration.get('GMV_ADMIN_USERNAME', 'admin'),
                                                      'password': configuration['GMV_ADMIN_PASSWORD']})
        login.raise_for_status()
        client.headers['X-CSRF-Token'] = login.json()['csrf']
        assets = client.post('/api/v1/search', json={'query': 'generator:novelai'}).json()['items']
        assert len(assets) == 1
        id = assets[0]['id']
        detail = client.get(f'/api/v1/assets/{id}').json()
        groups = client.get(f'/api/v1/assets/{id}/prompt-groups').json()
        references = client.get(f'/api/v1/assets/{id}/attachments').json()
        original = client.get(detail['original'])
        original.raise_for_status()
        assert len(detail['generation']['characters']) == 2
        assert groups['groups'] and references
        reference_hashes = []
        for reference in references:
            response = client.get(reference['url'])
            response.raise_for_status()
            reference_hashes.append(hashlib.sha256(response.content).hexdigest())
        results.append({'asset': id, 'original': hashlib.sha256(original.content).hexdigest(),
                        'generation': detail['generation'], 'groups': groups, 'reference_hashes': reference_hashes})
        client.post('/api/v1/auth/logout').raise_for_status()
        assert client.get(detail['original']).status_code == 401
        assert client.get(references[0]['url']).status_code == 401
assert results[0] == results[1], 'Source and restored protected media differ'
report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'source_port': 18083, 'restored_port': 18084,
          'original_sha256': results[0]['original'], 'characters': 2,
          'groups': len(results[0]['groups']['groups']), 'references': len(results[0]['reference_hashes']),
          'generation_groups_files_identical': True, 'logout_protection': True}
(root / 'docs/v02-restore-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
