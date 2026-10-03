"""Build a source + Windows launcher ZIP from an explicit allowlist, never local user data."""
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_ROOTS = {'backend', 'frontend', 'migrations', 'launcher', 'scripts', 'docs', '.github'}
ALLOWED_FILES = {'.env.example', '.gitignore', '.gitattributes', '.dockerignore', 'README.md', 'Dockerfile',
                 'compose.yaml', 'compose.benchmark.yaml', 'compose.watch-test.yaml', 'alembic.ini', 'pyproject.toml', 'uv.lock'}
DENIED_PARTS = {'__pycache__', '.pytest_cache', '.ruff_cache', 'node_modules', 'dist', 'data', 'backups',
                '.git', '.venv', 'test-results', 'playwright-report', 'test-artifacts'}


def allowed(relative):
    path = Path(relative)
    return (not path.is_absolute() and '..' not in path.parts and not DENIED_PARTS.intersection(path.parts)
            and (path.parts[0] in ALLOWED_ROOTS or relative in ALLOWED_FILES)
            and (not path.name.startswith('.env') or relative == '.env.example')
            and not path.name.endswith(('.pyc', '.log', '.tsbuildinfo', '.zip', '.exe', '.launcher-tmp', '.pem', '.key', '.pfx')))


def package():
    version = re.search(r'^version = "([^"]+)"', (ROOT / 'pyproject.toml').read_text(encoding='utf-8'), re.M)[1]
    executable = ROOT / 'dist/GenMediaVault.exe'
    if not executable.is_file() or executable.read_bytes()[:2] != b'MZ':
        raise RuntimeError('Build dist/GenMediaVault.exe with scripts/build_launcher.ps1 first')
    listed = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT).decode('utf-8').split('\0')
    files = {name: ROOT / name for name in listed if name and allowed(name)}
    files['GenMediaVault.exe'] = executable
    secrets = []
    env = ROOT / '.env'
    if env.exists():
        for line in env.read_text(encoding='utf-8-sig').splitlines():
            key, _, value = line.partition('=')
            if ('PASSWORD' in key or 'TOKEN' in key) and len(value.strip()) >= 12:
                secrets.append(value.strip().strip('"\'').encode())
    manifest = {'version': version, 'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
                'working_tree_dirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT)),
                'files': []}
    target = ROOT / f'dist/GenMediaVault-v{version}-Windows.zip'
    temporary = target.with_suffix('.tmp')
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, path in sorted(files.items()):
            if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
                raise RuntimeError(f'Unexpected link: {name}')
            data = path.read_bytes()
            if any(secret in data for secret in secrets):
                raise RuntimeError(f'Local credential found in release file: {name}')
            info = zipfile.ZipInfo('GenMediaVault/' + name.replace('\\', '/'), (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
            manifest['files'].append({'path': name.replace('\\', '/'), 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        archive.writestr('GenMediaVault/RELEASE-MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    temporary.replace(target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (ROOT / 'dist/SHA256SUMS.txt').write_text(f'{digest}  {target.name}\n', encoding='utf-8')
    print(json.dumps({'archive': str(target), 'files': len(files), 'bytes': target.stat().st_size, 'sha256': digest}, ensure_ascii=False))
    return target


if __name__ == '__main__':
    package()
