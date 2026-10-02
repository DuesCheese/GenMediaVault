"""Coordinate application file/database mutations with a consistent backup snapshot."""
import time
from contextlib import contextmanager

from sqlalchemy import text

from .db import engine

BACKUP_LOCK = 726341908120


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
