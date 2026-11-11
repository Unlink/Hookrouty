import base64

import httpx
import pytest

from hookrouty.app import create_app
from hookrouty.config import Settings


def settings_for(tmp_path, **kwargs):
    return Settings(
        app_secret_key=base64.urlsafe_b64encode(b"a" * 32).decode(),
        admin_password="initial-password",
        data_dir=tmp_path,
        **kwargs,
    )


async def test_health_uses_persistent_database(tmp_path):
    settings = settings_for(tmp_path)
    for _ in range(2):
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                assert (await client.get("/healthz")).json() == {"status": "ok"}
            tables = await app.state.db.all("SELECT name FROM sqlite_master WHERE type='table'")
            assert {
                "users",
                "sessions",
                "hooks",
                "hook_members",
                "executions",
                "aggregate_counters",
                "app_meta",
                "schema_migrations",
            } <= {r["name"] for r in tables}
    assert (tmp_path / "hookrouty.sqlite3").exists()


def test_invalid_key_rejected(tmp_path):
    with pytest.raises(ValueError):
        Settings(app_secret_key="invalid", data_dir=tmp_path)


async def test_bootstrap_password_required(tmp_path):
    settings = settings_for(tmp_path)
    settings.admin_password = ""
    app = create_app(settings)
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        async with app.router.lifespan_context(app):
            pass


def test_invalid_startup_never_logs_secret(tmp_path):
    import os
    import subprocess
    import sys

    env = {**os.environ, "APP_SECRET_KEY": "do-not-log-invalid-secret", "DATA_DIR": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "-m", "hookrouty"], env=env, capture_output=True, text=True, timeout=10
    )
    assert result.returncode != 0
    assert "do-not-log-invalid-secret" not in result.stdout + result.stderr
