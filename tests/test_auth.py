from conftest import login

from hookrouty.security import hash_password, verify_password


def test_password_hash():
    digest = hash_password("hello")
    assert digest.startswith("$argon2id$")
    assert verify_password("hello", digest)
    assert not verify_password("other", digest)


async def test_sessions_and_csrf(client):
    assert (await client.get("/api/hooks")).status_code == 401
    assert (await client.post("/api/login", json={})).status_code == 403
    before = (await client.get("/api/session")).json()
    assert (await login(client)).status_code == 200
    after = (await client.get("/api/session")).json()
    assert before["csrf_token"] != after["csrf_token"]
    assert after["user"]["role"] == "admin"
    assert (await client.post("/api/logout")).status_code == 204
    assert (await client.get("/api/users")).status_code == 401


async def test_login_rate_limit(client):
    for _ in range(5):
        assert (await login(client, password="wrong")).status_code == 401
    response = await login(client)
    assert response.status_code == 429
    assert response.headers["retry-after"]


async def test_password_change_invalidates_session(admin_client):
    response = await admin_client.post(
        "/api/me/password",
        json={"current_password": "initial-password", "password": "new-password"},
    )
    assert response.status_code == 204
    assert (await admin_client.get("/api/users")).status_code == 401
    assert (await login(admin_client, password="new-password")).status_code == 200


async def test_secretbox(client):
    box = client.app.state.box
    encrypted = box.encrypt("a-secret")
    assert "a-secret" not in encrypted
    assert box.decrypt(encrypted) == "a-secret"
