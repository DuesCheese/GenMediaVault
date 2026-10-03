"""Coordinate application file/database mutations with a consistent backup snapshot."""
import time
import json
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import text

from .db import engine

BACKUP_LOCK = 726341908120


def update_in_progress():
    from .config import settings
    root = settings().update_control_dir
    if not root:
        return False
    try:
        value = json.loads((Path(root) / 'status.json').read_text(encoding='utf-8'))
        return value.get('phase') in ('backing_up', 'deploying', 'rolling_back')
    except (OSError, ValueError):
        return False


@contextmanager
def mutation_gate(check, exclusive=False):
    suffix = "" if exclusive else "_shared"
    with engine().connect() as connection:
        acquired = False
        try:
            while not acquired:
                check()
                acquired = connection.scalar(text(f"SELECT pg_try_advisory_lock{suffix}(:key)"), {"key": BACKUP_LOCK})
                connection.commit()
                if not acquired:
                    time.sleep(0.25)
            yield connection
        finally:
            connection.rollback()
            if acquired:
                connection.execute(text(f"SELECT pg_advisory_unlock{suffix}(:key)"), {"key": BACKUP_LOCK})
                connection.commit()
