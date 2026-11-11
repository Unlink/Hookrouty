import base64

import httpx
import pytest

from hookrouty.app import create_app
from hookrouty.config import Settings


@pytest.fixture
async def client(tmp_path):
    app = create_app(
        Settings(
            app_secret_key=base64.urlsafe_b64encode(b"a" * 32).decode(),
            admin_password="initial-password",
            data_dir=tmp_path,
        )
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            client.app = app
            yield client


async def login(client, username="admin", password="initial-password"):
    session = (await client.get("/api/session")).json()
    client.headers["X-CSRF-Token"] = session["csrf_token"]
    response = await client.post("/api/login", json={"username": username, "password": password})
    if response.status_code == 200:
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response


@pytest.fixture
async def admin_client(client):
    assert (await login(client)).status_code == 200
    return client
