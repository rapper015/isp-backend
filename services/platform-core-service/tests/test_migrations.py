import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


def test_alembic_upgrades_empty_database(monkeypatch, tmp_path):
    database = tmp_path / "platform-core.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database.as_posix()}")
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("select name from sqlite_master where type='table'")}
    assert {"platform_users", "platform_refresh_tokens", "platform_service_accounts", "alembic_version"} <= tables
