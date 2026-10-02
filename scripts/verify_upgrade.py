"""Record/compare data fingerprints around the v0.2 migration without exposing private content."""
import argparse
import json
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('mode', choices=['before', 'after'])
args = parser.parse_args()
tables = ['users', 'user_assets', 'assets', 'physical_files', 'raw_metadata', 'generations',
          'collections', 'collection_assets', 'asset_tags', 'rules']
checks = {}
for table in tables:
    query = f"SELECT count(*), md5(string_agg(row_to_json(t)::text, E'\\n' ORDER BY row_to_json(t)::text)) FROM {table} t"
    result = subprocess.run(['docker', 'compose', 'exec', '-T', 'postgres', 'psql', '-U', 'genmedia',
                             '-d', 'genmedia', '-Atc', query], capture_output=True, text=True, check=True)
    checks[table] = result.stdout.strip()
target = Path('data/v02-preupgrade.json')
if args.mode == 'before':
    target.write_text(json.dumps(checks), encoding='utf-8')
    print('Saved pre-upgrade fingerprints for', len(checks), 'tables')
else:
    assert checks == json.loads(target.read_text(encoding='utf-8')), 'Data changed across upgrade; inspect before reparsing'
    print('All', len(checks), 'data tables unchanged across migration')
