from conftest import login


def hook_spec(path="/hooks/deploy/:project"):
    return {
        "name": "Deploy",
        "enabled": True,
        "input_path": path,
        "target": {
            "url": "http://upstream/apps/{{path.project}}",
            "headers": [{"name": "Authorization", "value": "Bearer super-secret", "secret": True}],
        },
    }


async def test_hook_crud_secrets_and_isolation(admin_client):
    client = admin_client
    response = await client.post("/api/hooks", json=hook_spec())
    assert response.status_code == 201, response.text
    created = response.json()
    hid = created["id"]
    assert created["token"]
    assert "super-secret" not in response.text
    hook = created["hook"]
    assert hook["target"]["headers"][0]["configured"]
    hook["target"]["headers"][0]["value_action"] = "keep"
    assert (await client.patch(f"/api/hooks/{hid}", json=hook)).status_code == 200
    assert (await client.post("/api/hooks", json=hook_spec())).status_code == 409
    copied = (
        await client.post(
            f"/api/hooks/{hid}/duplicate", json={"input_path": "/hooks/copy/:project"}
        )
    ).json()
    assert copied["token"] != created["token"]
    assert not copied["hook"]["enabled"]
    member = (
        await client.post("/api/users", json={"username": "member", "password": "member-password"})
    ).json()
    await client.put(f"/api/hooks/{hid}/members", json={"user_ids": [member["id"]]})
    await client.post("/api/logout")
    await login(client, "member", "member-password")
    assert len((await client.get("/api/hooks")).json()) == 1
    assert (await client.get("/api/hooks/" + copied["id"])).status_code == 404
    assert (await client.post("/api/hooks/" + copied["id"] + "/test", json={})).status_code == 404
    assert (
        await client.post(
            "/api/hooks/" + copied["id"] + "/duplicate", json={"input_path": "/hooks/other"}
        )
    ).status_code == 404
    assert (await client.delete(f"/api/hooks/{hid}")).status_code == 204
    assert (await client.get(f"/api/hooks/{hid}")).status_code == 404
    tombstone = await client.app.state.db.one("SELECT * FROM hooks WHERE id=?", (hid,))
    assert tombstone["config_json"] is None and tombstone["token_hash"] is None


async def test_public_auth_and_preview(admin_client):
    client = admin_client
    created = (await client.post("/api/hooks", json=hook_spec())).json()
    assert (await client.post("/hooks/deploy/test")).status_code == 401
    assert (await client.get("/hooks/deploy/test")).status_code == 405
    preview = await client.post(
        "/api/hooks/" + created["id"] + "/preview", json={"path": {"project": "test"}}
    )
    assert preview.status_code == 200, preview.text
    assert "super-secret" not in preview.text
    assert not await client.app.state.db.one("SELECT * FROM executions WHERE attempted_target=1")
