import json
from pathlib import Path

import pytest

from genmedia.translations import candidates, translation_key


def test_reference_snapshot_preserves_categories_and_normalizes_aliases():
    import runpy
    key = runpy.run_path('scripts/import_reference_tags.py')['key']
    source = Path('backend/genmedia/resources/tag-translations-v1.json')
    data = json.loads(source.read_text(encoding='utf-8'))
    entries = data['entries']
    assert len(entries) == 11073
    assert len({entry['key'] for entry in entries}) == len(entries)
    assert len({group for entry in entries for group in entry['groups']}) == 241
    assert all(key(entry['tag']) == entry['key'] == translation_key(entry['tag']) for entry in entries)
    assert any(len(entry['groups']) > 1 for entry in entries)
    assert translation_key(r'  Rem_\(RE:ZERO\) ') == 'rem (re:zero)'
    assert 'white hair' in set(candidates('1.5::white hair, blue eyes::'))


@pytest.mark.integration
def test_dictionary_crud_search_permissions_and_conflicts(clients):
    admin, alice = clients['admin'], clients['alice']
    body = {'tag': 'white_hair', 'translation': '白发 / 银白头发', 'groups': ['人物-头发', '颜色', '颜色']}
    assert alice.post('/api/v1/translations', json=body).status_code == 403
    response = admin.post('/api/v1/translations', json=body)
    assert response.status_code == 201, response.text
    item = response.json()
    assert item['groups'] == ['人物-头发', '颜色']
    assert admin.post('/api/v1/translations', json={**body, 'tag': 'WHITE HAIR'}).status_code == 409
    for query in ('白', '银白', 'white', 'WHITE_H', 'hair'):
        result = alice.get('/api/v1/translations', params={'q': query, 'limit': 1}).json()
        assert result['total'] == 1 and result['items'][0]['id'] == item['id']
    assert alice.get('/api/v1/translations', params={'q': '%'}).json()['total'] == 0
    assert alice.get('/api/v1/translations', params={'group': '人物-头发'}).json()['total'] == 1
    assert alice.get('/api/v1/translations/groups').json() == ['人物-头发', '颜色']
    assert alice.get('/api/v1/translations', params={'page': 2, 'limit': 1}).json()['items'] == []
    url = f"/api/v1/translations/{item['id']}"
    edit = {**body, 'translation': '白色头发', 'revision': 1}
    assert alice.put(url, json=edit).status_code == 403
    assert admin.put(url, json=edit).json()['revision'] == 2
    assert alice.get('/api/v1/translations', params={'q': '白发'}).json()['total'] == 1
    assert admin.put(url, json=edit).status_code == 409
    assert admin.delete(url, params={'revision': 1}).status_code == 409
    assert alice.delete(url, params={'revision': 2}).status_code == 403
    assert admin.delete(url, params={'revision': 2}).status_code == 200
    assert alice.get('/api/v1/translations', params={'q': '白'}).json()['total'] == 0
    assert admin.post('/api/v1/translations', json={**body, 'tag': '  '}).status_code == 422
    assert admin.post('/api/v1/translations', json={**body, 'translation': 'bad\x00value'}).status_code == 422


@pytest.mark.integration
def test_translation_preserves_weight_syntax_unknown_text_and_live_updates(clients):
    from genmedia.maintenance import mutation_gate
    admin, alice = clients['admin'], clients['alice']
    for tag, translation in [('white hair', '白发'), ('blue_eyes', '蓝眼'), ('rem (re:zero)', '蕾姆'), ('sky, sunset', '晚霞')]:
        assert admin.post('/api/v1/translations', json={'tag': tag, 'translation': translation, 'groups': ['测试分类']}).status_code == 201
    texts = ['WHITE_HAIR', '(white hair:1.25)', '{{blue eyes}}', '1.5::white hair, blue eyes::',
             r'rem \(re:zero\)', 'white hair ornament', 'a completely unknown prompt', r'sky\, sunset',
             '(white hair, unknown:0.8)', 'blue eyes']
    expected = ['白发', '(白发:1.25)', '{{蓝眼}}', '1.5::白发, 蓝眼::', '蕾姆',
                texts[5], texts[6], '晚霞', '(白发, unknown:0.8)', '蓝眼']
    with mutation_gate(lambda: None, exclusive=True):
        response = alice.post('/api/v1/translations/lookup', json={'texts': texts})
    assert response.status_code == 200, response.text
    result = response.json()['items']
    assert [item['translated'] for item in result] == expected
    assert result[5]['matched'] is False
    assert result[0]['groups'] == ['测试分类']
    entry = admin.get('/api/v1/translations', params={'q': 'blue eyes'}).json()['items'][0]
    admin.put(f"/api/v1/translations/{entry['id']}", json={'tag': entry['tag'], 'translation': '蓝色眼睛', 'groups': [], 'revision': 1})
    assert alice.post('/api/v1/translations/lookup', json={'texts': ['blue eyes']}).json()['items'][0]['translated'] == '蓝色眼睛'
    assert alice.post('/api/v1/translations/lookup', json={'texts': ['a'] * 1001}).status_code == 422
    alice.post('/api/v1/auth/logout')
    assert alice.get('/api/v1/translations').status_code == 401
    assert alice.post('/api/v1/translations/lookup', json={'texts': ['white hair']}).status_code == 401
