from conftest import login


async def test_web_pages_and_escaping(client):
    assert (await client.get("/app/hooks")).status_code == 303
    assert (await client.get("/login")).status_code == 200
    await login(client)
    for path in ["/app/hooks", "/app/hooks/new", "/app/executions", "/app/users", "/app/profile"]:
        response = await client.get(path)
        assert response.status_code == 200, path
        assert "Hookrouty" in response.text
        assert "content-security-policy" in response.headers
    assert (await client.get("/hooks/new")).status_code == 404


async def test_invalid_configuration_is_safe(admin_client):
    response = await admin_client.post(
        "/api/hooks",
        json={
            "name": "secret-data",
            "target": {"url": "http://{{query.host}}/"},
            "input_path": "/hooks/test",
        },
    )
    assert response.status_code == 400
    assert "secret-data" not in response.text


async def test_curl_page_is_scoped_and_never_reveals_or_changes_tokens(admin_client):
    client = admin_client
    created = (
        await client.post(
            "/api/hooks",
            json={
                "name": "Usage <script>alert(1)</script>",
                "input_path": "/hooks/use/:project",
                "auth": {"token": "incoming-private"},
                "target": {
                    "url": "http://upstream/",
                    "headers": [
                        {"name": "Authorization", "value": "destination-private", "secret": True}
                    ],
                },
            },
        )
    ).json()
    hid = created["id"]
    before = await client.app.state.db.one("SELECT token_hash FROM hooks WHERE id=?", (hid,))
    response = await client.get("/app/hooks/" + hid + "/curl")
    assert response.status_code == 200, response.text
    assert 'data-page="curl"' in response.text
    assert "incoming-private" not in response.text and "destination-private" not in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert "curl-command" in response.text
    assert before == await client.app.state.db.one(
        "SELECT token_hash FROM hooks WHERE id=?", (hid,)
    )
    assert not await client.app.state.db.one("SELECT * FROM executions")
    await client.post("/api/users", json={"username": "outsider", "password": "member-password"})
    await client.post("/api/logout")
    await login(client, "outsider", "member-password")
    assert (await client.get("/app/hooks/" + hid + "/curl")).status_code == 404
    await client.post("/api/logout")
    assert (await client.get("/app/hooks/" + hid + "/curl")).status_code == 303
