from alembic import context
from genmedia.db import Base, engine
from genmedia import models  # noqa: F401

with engine().connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
