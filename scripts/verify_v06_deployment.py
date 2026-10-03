"""Stage and run an isolated release checkout; never change the user's live Compose project."""
import json
import os
import secrets
import shutil
import subprocess

from package_release import ROOT, allowed

STAGE = ROOT / 'data/v06-deployment'
STAGE.mkdir(parents=True, exist_ok=True)
listed = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0')
for name in listed:
    if name and allowed(name):
        destination = STAGE / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
env = STAGE / '.env'
if not env.exists():
    env.write_text(f'POSTGRES_PASSWORD={secrets.token_hex(24)}\nGMV_ADMIN_PASSWORD={secrets.token_hex(24)}\n'
                   'GMV_ADMIN_USERNAME=admin\nGMV_PORT=18086\nGMV_BIND=127.0.0.1\nCOMPOSE_PROJECT_NAME=gmv-v06-test\n', encoding='utf-8')
subprocess.run(['docker', 'compose', 'up', '-d', '--build', '--wait', '--wait-timeout', '300'], cwd=STAGE, check=True)
print(json.dumps({'url': 'http://127.0.0.1:18086', 'workspace': str(STAGE)}, ensure_ascii=False))
if os.environ.get('GMV_RUN_BROWSER'):
    from dotenv import dotenv_values
    config = dotenv_values(env)
    environment = {**os.environ, 'GMV_E2E_URL': 'http://127.0.0.1:18086', 'GMV_E2E_PASSWORD': config['GMV_ADMIN_PASSWORD']}
    subprocess.run(['npm.cmd' if os.name == 'nt' else 'npm', 'run', 'test:e2e'], cwd=ROOT / 'frontend', env=environment, check=True)
