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
