"""Regenerate ONLY before the initial migration has ever been released."""
from pathlib import Path

from sqlalchemy import create_mock_engine

from genmedia.db import Base
from genmedia import models  # noqa: F401

statements = []
engine = create_mock_engine("postgresql+psycopg://", lambda sql, *args, **kwargs:
                            statements.append(str(sql.compile(dialect=engine.dialect)).strip() + ";"))
Base.metadata.create_all(engine)
statements.insert(0, "CREATE EXTENSION IF NOT EXISTS pg_trgm;")
for table, column in (("generations", "prompt"), ("generations", "negative"), ("assets", "filename")):
    statements.append(f"CREATE INDEX ix_{table}_{column}_trgm ON {table} USING gin ({column} gin_trgm_ops);")
for column in ("model", "generator", "sampler"):
    statements.append(f"CREATE INDEX ix_generations_{column}_lower ON generations (lower({column}));")
statements.extend([
    "ALTER TABLE user_assets ADD CONSTRAINT ck_rating CHECK (rating BETWEEN 1 AND 5);",
    "ALTER TABLE users ADD CONSTRAINT ck_role CHECK (role IN ('admin', 'user'));",
    "ALTER TABLE libraries ADD CONSTRAINT ck_library_mode CHECK (mode IN ('managed', 'indexed'));",
    "ALTER TABLE assets ADD CONSTRAINT ck_dimensions CHECK (width > 0 AND height > 0);",
])
Path("migrations/initial_schema.sql").write_text("\n\n".join(statements) + "\n", encoding="utf-8")
