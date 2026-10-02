"""Run browser checks on the isolated v0.2 Compose site; keep credentials out of terminal output."""
import os
import subprocess
from pathlib import Path

from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
config = dotenv_values(root / '.env')
environment = {**os.environ, 'GMV_E2E_URL': 'http://127.0.0.1:18083',
               'GMV_E2E_PASSWORD': config['GMV_ADMIN_PASSWORD']}
raise SystemExit(subprocess.run(['npm.cmd' if os.name == 'nt' else 'npm', 'run', 'test:e2e'],
                               cwd=root / 'frontend', env=environment).returncode)
