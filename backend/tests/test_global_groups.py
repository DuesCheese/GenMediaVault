import io
import json
import zipfile
from uuid import uuid4

import pytest

from test_integration import upload
from genmedia.worker import run_one

pytestmark = pytest.mark.integration


def create_group(client, name='外观', tags=()):
    response = client.post('/api/v1/tag-groups', json={'name': name, 'color': '#8fbcbb'})
    assert response.status_code == 201, response.text
    group = response.json()
    for tag in tags:
        assert client.put(f"/api/v1/tag-groups/{group['id']}/tags", json={'tag': tag, 'revision': group['revision']}).status_code == 200
        group = client.get(f"/api/v1/tag-groups/{group['id']}/tags").json()['group']
    return group


def save(client, asset, layout, publish=None):
    return client.put(f'/api/v1/assets/{asset}/prompt-groups', json={
        'revision': layout['revision'], 'catalog_revision': layout['catalog_revision'],
        'groups': layout['groups'], 'publish_group_id': publish})


def get_layout(client, asset):
    response = client.get(f'/api/v1/assets/{asset}/prompt-groups')
    assert response.status_code == 200, response.text
    return response.json()


def test_dictionary_sync_idempotent_and_preserves_manual_members(clients):
    admin, alice = clients['admin'], clients['alice']
    group = create_group(alice, tags=['green eyes'])
    for tag, meaning, groups in [('white hair', '白发', ['外观', '头发']), ('blue eyes', '蓝眼', ['外观']), ('smile', '微笑', [])]:
        assert admin.post('/api/v1/translations', json={'tag': tag, 'translation': meaning, 'groups': groups}).status_code == 201
    assert alice.post('/api/v1/translations/sync-tags').status_code == 403
    for expected in (4, 0):
        result = admin.post('/api/v1/translations/sync-tags')
        assert result.status_code == 200, result.text
        assert result.json() == {'groups': 3, 'added': expected}
    assert len(alice.get('/api/v1/tag-groups').json()) == 3
    detail = alice.get(f"/api/v1/tag-groups/{group['id']}/tags").json()
    assert {t['tag'] for t in detail['items']} == {'white hair', 'blue eyes', 'green eyes'}
    assert alice.get('/api/v1/tag-groups', params={'q': '白发'}).json()
    assert alice.get(f"/api/v1/tag-groups/{group['id']}/tags", params={'q': 'WHITE_H'}).json()['total'] == 1
    assert alice.get(f"/api/v1/tag-groups/{group['id']}/tags", params={'q': '%'}).json()['total'] == 0
    assert len(alice.get(f"/api/v1/tag-groups/{group['id']}/tags", params={'limit': 1, 'page': 2}).json()['items']) == 1


def test_publish_merge_remove_and_auto_group_new_images(clients):
    alice, bob = clients['alice'], clients['bob']
    global_group = create_group(alice, tags=['green eyes'])
    first = upload(alice, prompt='(white hair:1.2), blue eyes')
    layout = get_layout(alice, first)
    local = {'id': str(uuid4()), 'name': '外观', 'color': '#b48ead', 'source': 'base:positive',
             'tokens': layout['sources'][0]['tokens']}
    layout['groups'] = [local]
    published = save(alice, first, layout, local['id'])
    assert published.status_code == 200, published.text
    assert len(alice.get('/api/v1/tag-groups').json()) == 1
    members = alice.get(f"/api/v1/tag-groups/{global_group['id']}/tags").json()['items']
    assert {t['tag'] for t in members} == {'green eyes', 'white hair', 'blue eyes'}
    second = upload(bob, prompt='{{white_hair}}, blue eyes, unknown tag', seed='123')
    automatic = get_layout(bob, second)
    assert len(automatic['groups']) == 1
    assert [t['text'] for t in automatic['groups'][0]['tokens']] == ['{{white_hair}}', 'blue eyes']
    assert automatic['groups'][0]['global_group_id'] == global_group['id']
    # Removing a token only removes that membership, preserving tags absent from this image.
    layout = published.json()
    layout['groups'][0]['tokens'] = layout['groups'][0]['tokens'][:1]
    response = save(alice, first, layout)
    assert response.status_code == 200, response.text
    members = alice.get(f"/api/v1/tag-groups/{global_group['id']}/tags").json()['items']
    assert {t['tag'] for t in members} == {'green eyes', 'white hair'}
    assert [t['text'] for t in get_layout(bob, second)['groups'][0]['tokens']] == ['{{white_hair}}']
    # An edit opened before the deletion cannot put it back.
    assert save(bob, second, automatic).status_code == 409
    assert alice.get(f'/api/v1/assets/{first}').json()['generation']['prompt'] == '(white hair:1.2), blue eyes'
    # Add a new selected tag from the image to the existing global group.
    fresh = get_layout(bob, second)
    fresh['groups'][0]['tokens'].append(fresh['sources'][0]['tokens'][2])
    assert save(bob, second, fresh).status_code == 200
    assert 'unknown tag' in {t['tag'] for t in alice.get(f"/api/v1/tag-groups/{global_group['id']}/tags").json()['items']}
    job = alice.post('/api/v1/exports', json={'asset_ids': [first], 'include_metadata': True}).json()['job_id']
    assert run_one()
    with zipfile.ZipFile(io.BytesIO(alice.get(f'/api/v1/jobs/{job}/download').content)) as archive:
        metadata = json.loads(archive.read(next(name for name in archive.namelist() if name.endswith('.png.json'))))
        assert [t['text'] for t in metadata['prompt_groups'][0]['tokens']] == ['(white hair:1.2)']


def test_catalog_crud_conflicts_and_image_projection(clients):
    alice, admin = clients['alice'], clients['admin']
    group = create_group(alice, tags=['white hair'])
    asset = upload(alice)
    stale = get_layout(alice, asset)
    url = f"/api/v1/tag-groups/{group['id']}"
    assert alice.put(url + '/tags', json={'tag': 'x' * 2001, 'revision': group['revision']}).status_code == 422
    assert alice.post('/api/v1/tag-groups', json={'name': ' 外观 '}).status_code == 409
    assert alice.put(url + '/tags', json={'tag': 'WHITE_HAIR', 'revision': group['revision']}).status_code == 409
    assert alice.put(url + '/tags', json={'tag': 'blue eyes', 'previous_key': 'white hair', 'revision': group['revision']}).status_code == 200
    assert alice.put(url + '/tags', json={'tag': 'smile', 'revision': group['revision']}).status_code == 409
    assert [t['text'] for t in get_layout(alice, asset)['groups'][0]['tokens']] == ['blue eyes']
    assert save(alice, asset, stale).status_code == 409
    group = alice.get(url + '/tags').json()['group']
    assert alice.put(url, json={'name': '眼睛', 'color': '#a3be8c', 'revision': group['revision']}).status_code == 200
    layout = get_layout(alice, asset)
    assert (layout['groups'][0]['name'], layout['groups'][0]['color']) == ('眼睛', '#a3be8c')
    group = alice.get(url + '/tags').json()['group']
    assert alice.delete(url, params={'revision': group['revision']}).status_code == 403
    assert admin.delete(url, params={'revision': group['revision']}).status_code == 200
    assert not get_layout(alice, asset)['groups']
    assert save(alice, asset, layout).status_code == 409
    alice.post('/api/v1/auth/logout')
    assert alice.get('/api/v1/tag-groups').status_code == 401
    assert alice.post('/api/v1/tag-groups', json={'name': 'blocked'}).status_code == 401


def test_manual_precedence_multiple_groups_and_sources(clients):
    alice = clients['alice']
    asset = upload(alice, prompt='white hair, blue eyes')
    layout = get_layout(alice, asset)
    local = {'id': str(uuid4()), 'name': '人工保留', 'color': '#b48ead', 'source': 'base:positive',
             'tokens': layout['sources'][0]['tokens'][:1]}
    layout['groups'] = [local]
    assert save(alice, asset, layout).status_code == 200
    create_group(alice, '外观', ['white hair', 'blue eyes', 'bad hands'])
    create_group(alice, '眼睛', ['blue eyes'])
    layout = get_layout(alice, asset)
    assert layout['groups'][0] == local
    assert [g['source'] for g in layout['groups']].count('base:negative') == 1
    assert sum(t['text'] == 'blue eyes' for g in layout['groups'] for t in g['tokens']) == 2
    assert sum(t['text'] == 'white hair' for g in layout['groups'] for t in g['tokens']) == 1
    assert save(alice, asset, layout).status_code == 200
    assert get_layout(alice, asset)['groups'] == layout['groups']
    layout = get_layout(alice, asset)
    layout['groups'].reverse()
    assert save(alice, asset, layout).status_code == 200
    assert get_layout(alice, asset)['groups'] == layout['groups']


def test_removal_propagates_across_sources_and_exact_weighted_members(clients):
    alice = clients['alice']
    group = create_group(alice, tags=['bad hands', '(white hair:1.2)'])
    asset = upload(alice, prompt='bad hands, (white hair:1.2)')
    layout = get_layout(alice, asset)
    positive = next(g for g in layout['groups'] if g['source'] == 'base:positive')
    positive['tokens'] = []
    result = save(alice, asset, layout)
    assert result.status_code == 200, result.text
    assert not alice.get(f"/api/v1/tag-groups/{group['id']}/tags").json()['items']
    assert not any(g['tokens'] for g in get_layout(alice, asset)['groups'])


def test_new_manual_override_does_not_delete_global_membership(clients):
    alice = clients['alice']
    group = create_group(alice, tags=['white hair'])
    asset = upload(alice)
    layout = get_layout(alice, asset)
    token = layout['groups'][0]['tokens'][0]
    layout['groups'][0]['tokens'] = []
    layout['groups'].append({'id': str(uuid4()), 'name': '本图外观', 'color': '#b48ead', 'source': 'base:positive', 'tokens': [token]})
    assert save(alice, asset, layout).status_code == 200
    assert alice.get(f"/api/v1/tag-groups/{group['id']}/tags").json()['total'] == 1
    other = upload(alice, seed='258')
    assert get_layout(alice, other)['groups'][0]['tokens'][0]['text'] == 'white hair'
