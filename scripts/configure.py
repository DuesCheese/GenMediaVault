"""Create local deployment credentials without printing them or replacing an existing .env."""
import secrets
from pathlib import Path

target = Path(".env")
if target.exists():
    raise SystemExit(".env 已存在，未覆盖。")
target.write_text(
    f"POSTGRES_PASSWORD={secrets.token_urlsafe(32)}\nGMV_ADMIN_USERNAME=admin\n"
    f"GMV_ADMIN_PASSWORD={secrets.token_urlsafe(24)}\nGMV_BIND=127.0.0.1\n"
    "GMV_PORT=8080\nGMV_SECURE_COOKIE=false\nGMV_IMPORT_ROOT=./data/imports\n", encoding="utf-8")
Path("data/imports").mkdir(parents=True, exist_ok=True)
print("已创建 .env。管理员密码保存在 GMV_ADMIN_PASSWORD 中；此文件不会纳入版本控制。")
