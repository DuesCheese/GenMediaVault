"""Development preview on a real PostgreSQL database; not a production startup path."""
import argparse

from dev_database import environment

parser = argparse.ArgumentParser()
parser.add_argument("mode", choices=["app", "worker"])
args = parser.parse_args()
environment("genmedia_dev")

if args.mode == "app":
    from alembic import command
    from alembic.config import Config
    from genmedia.config import settings
    from genmedia.db import session_factory
    from genmedia.security import bootstrap
    import uvicorn
    command.upgrade(Config("alembic.ini"), "head")
    with session_factory()() as db:
        bootstrap(db, "admin", settings().admin_password)
    uvicorn.run("genmedia.main:app", host="127.0.0.1", port=8080)
else:
    from genmedia.worker import main
    main()
