"""Cross-platform Docker backup/restore; binary streams never pass through shell redirection."""
import argparse
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path, PurePosixPath


def command(*args, **kwargs):
    return subprocess.run(["docker", "compose", *args], check=True, **kwargs)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create(target):
    target.mkdir(parents=True, exist_ok=False)
    command("stop", "app", "worker")
    try:
        with (target / "database.dump").open("wb") as output:
            command("exec", "-T", "postgres", "pg_dump", "-U", "genmedia", "-d", "genmedia", "-Fc", stdout=output)
        with (target / "media.tar").open("wb") as output:
            command("run", "--rm", "--no-deps", "--user", "root", "--entrypoint", "tar", "app", "-C", "/data", "-cf", "-", ".", stdout=output)
        manifest = {"version": 1, "files": {name: sha256(target / name) for name in ("database.dump", "media.tar")},
                    "indexed_originals_included": False, "config_included": False}
        (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    finally:
        command("start", "app", "worker")
    print(f"备份完成：{target}")


def restore(source):
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("version") != 1:
        raise RuntimeError("不支持的备份版本")
    for name in ("database.dump", "media.tar"):
        if sha256(source / name) != manifest["files"][name]:
            raise RuntimeError(f"备份校验失败：{name}")
    with tarfile.open(source / "media.tar") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                raise RuntimeError("媒体备份包含不安全的路径或特殊文件")
    command("stop", "app", "worker")
    result = command("exec", "-T", "postgres", "psql", "-U", "genmedia", "-d", "genmedia", "-Atc",
                     "SELECT count(*) FROM pg_tables WHERE schemaname='public'", capture_output=True, text=True)
    if result.stdout.strip() != "0":
        raise RuntimeError("恢复只允许空数据库。请使用新的 Compose 项目和新的数据卷，避免覆盖现有资料。")
    command("run", "--rm", "--no-deps", "-T", "--entrypoint", "python", "app", "-c",
            "from pathlib import Path; "
            "assert not any(Path('/data').iterdir()), 'Media volume must also be empty before restore'")
    with (source / "database.dump").open("rb") as input:
        command("exec", "-T", "postgres", "pg_restore", "-U", "genmedia", "-d", "genmedia", "--no-owner", "--no-privileges", "--exit-on-error", stdin=input)
    with (source / "media.tar").open("rb") as input:
        command("run", "--rm", "--no-deps", "-T", "--user", "root", "--entrypoint", "tar", "app", "-C", "/data", "-xf", "-", stdin=input)
    print("恢复完成。重新挂载索引库目录后执行 docker compose up -d。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["create", "restore"])
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    (create if args.action == "create" else restore)(args.directory.resolve())
