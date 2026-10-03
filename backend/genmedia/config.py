from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GMV_", env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://genmedia:genmedia@localhost:5432/genmedia"
    data_dir: Path = Path("data")
    import_roots: list[Path] = []
    frontend_dir: Path = Path("frontend/dist")
    secure_cookie: bool = False
    admin_username: str = "admin"
    admin_password: str = ""
    session_hours: int = 24
    upload_limit_mb: int = 64
    metadata_limit_mb: int = 8
    scan_interval_seconds: int = 300
    stability_seconds: float = 2.0
    lease_seconds: int = 90
    update_control_dir: str = ""
    pg_dump_binary: str = "pg_dump"

    def prepare(self):
        for child in ("originals", "staging", "thumbnails", "exports", "attachments", "backups"):
            (self.data_dir / child).mkdir(parents=True, exist_ok=True)


@lru_cache
def settings() -> Settings:
    return Settings()
