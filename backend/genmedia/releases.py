"""Read official release metadata and verify the complete source bundle."""
import hashlib
import io
import json
import re
import urllib.request
import zipfile
from pathlib import PurePosixPath

REPOSITORY = 'DuesCheese/GenMediaVault'
API = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
MAX_ZIP = 128 * 1024 * 1024


def fetch(url, limit=MAX_ZIP):
    request = urllib.request.Request(url, headers={'User-Agent': 'GenMediaVault-Updater', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        value = response.read(limit + 1)
    if len(value) > limit:
        raise ValueError('下载内容超过大小限制')
    return value


def version_tuple(version):
    if not re.fullmatch(r'v?\d+\.\d+\.\d+', version):
        raise ValueError('版本号无效')
    return tuple(int(n) for n in version.lstrip('v').split('.'))


def latest_release():
    value = json.loads(fetch(API, 2 * 1024 * 1024))
    tag = value['tag_name']
    version_tuple(tag)
    if value['draft'] or value['prerelease']:
        raise ValueError('没有可用的正式版本')
    files = {}
    for name in (f'GenMediaVault-{tag}-Windows.zip', 'SHA256SUMS.txt'):
        item = next((a for a in value['assets'] if a['name'] == name), None)
        expected = f'https://github.com/{REPOSITORY}/releases/download/{tag}/{name}'
        if item is None or item['browser_download_url'] != expected:
            raise ValueError('正式版本缺少可信的发布附件')
        files[name] = expected
    return {'version': tag.lstrip('v'), 'tag': tag, 'url': value['html_url'], 'notes': value.get('body', ''), 'files': files}


def release_files(release):
    name = f'GenMediaVault-{release["tag"]}-Windows.zip'
    sums = fetch(release['files']['SHA256SUMS.txt'], 8192).decode('utf-8')
    expected = next((line.split()[0] for line in sums.splitlines() if len(line.split()) == 2 and line.split()[1] == name), '')
    data = fetch(release['files'][name])
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('发布包校验失败')
    return verified_bundle(data, release['version'])


def verified_bundle(data, version):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(i.file_size for i in archive.infolist()) > 512 * 1024 * 1024:
            raise ValueError('解压体积超过限制')
        manifest = json.loads(archive.read('GenMediaVault/RELEASE-MANIFEST.json'))
        if manifest['version'] != version or manifest['working_tree_dirty']:
            raise ValueError('发布包版本或源码状态不正确')
        files = {}
        for item in manifest['files']:
            path = PurePosixPath(item['path'])
            if path.is_absolute() or '..' in path.parts or '\\' in str(path) or ':' in str(path) or not path.parts:
                raise ValueError('发布包路径无效')
            if any(p in ('.git', 'data', 'backups', '.venv', 'node_modules', '.env') for p in path.parts):
                raise ValueError('发布包包含运行数据路径')
            if str(path) in files:
                raise ValueError('发布包存在重复路径')
            content = archive.read('GenMediaVault/' + str(path))
            if len(content) != item['size'] or hashlib.sha256(content).hexdigest() != item['sha256']:
                raise ValueError('发布文件校验失败')
            files[str(path)] = content
        if not {'compose.yaml', 'Dockerfile', 'pyproject.toml'}.issubset(files):
            raise ValueError('发布包缺少部署文件')
        return files
