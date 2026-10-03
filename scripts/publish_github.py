"""Publish the reviewed source and ZIP using Git Credential Manager; never print its token."""
import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def publish(owner, name):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', owner) or not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
        raise ValueError('Invalid repository name')
    git = shutil.which('git')
    helper = shutil.which('git-credential-manager')
    if not helper:
        helper = str(Path(git).resolve().parents[1] / 'mingw64/bin/git-credential-manager.exe')
    if not Path(helper).is_file():
        raise RuntimeError('Git Credential Manager is required; log in before publishing')
    environment = {**os.environ, 'GCM_INTERACTIVE': 'never', 'GIT_TERMINAL_PROMPT': '0'}
    credentials = subprocess.run([helper, 'get'], input=f'protocol=https\nhost=github.com\nusername={owner}\n\n',
                                 capture_output=True, text=True, encoding='utf-8', env=environment, check=True)
    fields = dict(line.split('=', 1) for line in credentials.stdout.splitlines() if '=' in line)
    token = fields.get('password')
    if not token:
        raise RuntimeError('GitHub login is required')
    version = re.search(r'^version = "([^"]+)"', (ROOT / 'pyproject.toml').read_text(encoding='utf-8'), re.M)[1]
    archive = ROOT / f'dist/GenMediaVault-v{version}-Windows.zip'
    checksums = ROOT / 'dist/SHA256SUMS.txt'
    if hashlib.sha256(archive.read_bytes()).hexdigest() != checksums.read_text().split()[0]:
        raise RuntimeError('Release archive checksum mismatch')
    if subprocess.check_output([git, 'status', '--porcelain'], cwd=ROOT):
        raise RuntimeError('Commit reviewed source before publishing')
    head = subprocess.check_output([git, 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip()
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read('GenMediaVault/RELEASE-MANIFEST.json'))
        if manifest['source_commit'] != head or manifest['working_tree_dirty']:
            raise RuntimeError('Rebuild the ZIP from the committed source before publishing')
    repository = f'{owner}/{name}'
    headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
    with httpx.Client(base_url='https://api.github.com', headers=headers, timeout=120) as client:
        profile = client.get('/user')
        profile.raise_for_status()
        if profile.json()['login'].lower() != owner.lower():
            raise RuntimeError('Authenticated GitHub user does not match the requested owner')
        response = client.get(f'/repos/{repository}')
        if response.status_code == 404:
            response = client.post('/user/repos', json={'name': name, 'private': False, 'auto_init': False,
                'description': 'Self-hosted AI image vault: metadata, search, prompt groups, local tag translations and Windows launcher.'})
        response.raise_for_status()
        if response.json()['private']:
            raise RuntimeError('Existing repository is private; visibility was not changed')
        url = response.json()['clone_url']
        remotes = subprocess.check_output([git, 'remote'], cwd=ROOT).decode().splitlines()
        if 'origin' in remotes:
            existing = subprocess.check_output([git, 'remote', 'get-url', 'origin'], cwd=ROOT).decode().strip()
            if existing != url:
                raise RuntimeError('Existing origin differs from requested repository; not overwritten')
        else:
            subprocess.run([git, 'remote', 'add', 'origin', url], cwd=ROOT, check=True)
        # Git invokes a fixed, locally discovered helper; credentials never enter arguments or remote URLs.
        command = [git, '-c', 'credential.helper=', '-c', 'credential.helper=!' + shlex.quote(helper.replace('\\', '/'))]
        subprocess.run([*command, 'push', '-u', 'origin', 'HEAD:main'], cwd=ROOT, env=environment, check=True)
        branch = client.get(f'/repos/{repository}/commits/main')
        branch.raise_for_status()
        if branch.json()['sha'] != head:
            raise RuntimeError('Remote main does not match the reviewed commit')
        tag = 'v' + version
        releases = client.get(f'/repos/{repository}/releases', params={'per_page': 100})
        releases.raise_for_status()
        release = next((item for item in releases.json() if item['tag_name'] == tag), None)
        if release and not release['draft']:
            raise RuntimeError('This release is already published; it was not changed')
        if not release:
            response = client.post(f'/repos/{repository}/releases', json={'tag_name': tag, 'target_commitish': head,
                'name': f'GenMedia Vault {tag}', 'draft': True, 'prerelease': False,
                'body': (ROOT / f'docs/RELEASE-{tag}.md').read_text(encoding='utf-8')})
            response.raise_for_status()
            release = response.json()
        elif release['target_commitish'] != head:
            raise RuntimeError('Existing draft targets a different commit')
        assets = client.get(f"/repos/{repository}/releases/{release['id']}/assets")
        assets.raise_for_status()
        existing_names = {item['name']: item for item in assets.json()}
        for path in (archive, checksums):
            expected = hashlib.sha256(path.read_bytes()).hexdigest()
            if path.name in existing_names:
                if existing_names[path.name].get('digest') != 'sha256:' + expected:
                    raise RuntimeError('Existing draft asset differs; it was not overwritten')
                continue
            response = client.post(release['upload_url'].split('{')[0], params={'name': path.name},
                                   headers={'Content-Type': 'application/octet-stream'}, content=path.read_bytes())
            response.raise_for_status()
            if response.json().get('digest') not in (None, 'sha256:' + expected):
                raise RuntimeError('Uploaded asset checksum mismatch')
        response = client.patch(f"/repos/{repository}/releases/{release['id']}", json={'draft': False})
        response.raise_for_status()
        print(json.dumps({'repository': f'https://github.com/{repository}', 'release': response.json()['html_url'], 'commit': head}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--owner', required=True)
    parser.add_argument('--name', required=True)
    arguments = parser.parse_args()
    publish(arguments.owner, arguments.name)
