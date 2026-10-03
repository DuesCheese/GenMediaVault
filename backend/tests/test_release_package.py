import runpy
from pathlib import Path


def test_release_allowlist_excludes_runtime_data_and_credentials():
    root = Path(__file__).resolve().parents[2]
    allowed = runpy.run_path(str(root / 'scripts/package_release.py'))['allowed']
    for path in ('.env', 'backend/.env.production', 'frontend/.env.local', 'backups/full.zip', 'data/image.png',
                 'frontend/node_modules/react/index.js', 'frontend/dist/app.js', '.git/config', 'dist/tool.exe',
                 'backend/__pycache__/api.pyc', 'backend/private.pem', 'scripts/private.key', '../README.md'):
        assert not allowed(path), path
    for path in ('.env.example', 'compose.yaml', 'Dockerfile', 'backend/genmedia/main.py', 'launcher/Program.cs',
                 'backend/genmedia/resources/tag-translations-v1.json', 'frontend/package-lock.json'):
        assert allowed(path), path
