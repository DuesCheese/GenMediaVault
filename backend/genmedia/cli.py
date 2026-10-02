import argparse
import json
from pathlib import Path

from .config import settings
from .db import session_factory
from .security import bootstrap


def main():
    parser = argparse.ArgumentParser(prog="genmedia")
    parser.add_argument("command", choices=["bootstrap", "worker", "openapi", "restore-backup"])
    parser.add_argument("archive", nargs="?", type=Path)
    args = parser.parse_args()
    settings().prepare()
    if args.command == "bootstrap":
        with session_factory()() as db:
            created = bootstrap(db, settings().admin_username, settings().admin_password)
            print("管理员已创建" if created else "管理员已存在，未修改密码")
    elif args.command == "worker":
        from .worker import main as worker
        worker()
    elif args.command == "restore-backup":
        if not args.archive:
            parser.error("restore-backup 需要提供备份 ZIP 路径")
        from .snapshots import restore_snapshot
        restore_snapshot(args.archive)
    else:
        from .main import app
        target = Path("docs/openapi.json")
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(target)


if __name__ == "__main__":
    main()
