import json
import sqlite3
from pathlib import Path

import pytest
from conftest import login


def custom_template(**changes):
    value = {
        "name": "Custom notification",
        "description": "Reusable HTTP notification",
        "fields": [
            {"key": "url", "label": "URL", "kind": "url", "required": True},
            {"key": "credential", "label": "API token", "kind": "secret", "required": True},
        ],
        "target": {
            "method": "POST",
            "url": "{{inputs.url}}",
            "headers": [{"name": "Authorization", "value": "Bearer {{inputs.credential}}"}],
            "body": {"mode": "json", "template": {"event": "deploy"}},
        },
    }
    value.update(changes)
    return value


async def test_seeded_templates_and_safe_preview(admin_client):
    client = admin_client
    templates = (await client.get("/api/templates")).json()
    assert {t["id"] for t in templates} >= {"simple-http-url", "dockhand-redeploy"}
    response = await client.post(
        "/api/templates/dockhand-redeploy/preview",
        json={
            "name": "Deploy",
            "values": {
                "url": "http://dockhand:3000",
                "stack": "myapp",
                "token": "dh-private",
                "environment": "2",
            },
        },
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["url"] == "http://dockhand:3000/api/stacks/myapp/deploy?env=2"
    assert "dh-private" not in response.text
    assert preview["headers"]["Authorization"] == "[REDACTED]"
    assert json.loads(preview["body"]["text"]) == {
        "pull": True,
        "build": False,
        "forceRecreate": True,
    }
    assert not await client.app.state.db.one("SELECT * FROM hooks")
    assert not await client.app.state.db.one("SELECT * FROM executions")


async def test_admin_can_add_template_and_member_can_use_it(admin_client):
    client = admin_client
    response = await client.post("/api/templates", json=custom_template())
    assert response.status_code == 201, response.text
    tid = response.json()["id"]
    await client.post("/api/users", json={"username": "member", "password": "member-password"})
    await client.post("/api/logout")
    await login(client, "member", "member-password")
    assert tid in [t["id"] for t in (await client.get("/api/templates")).json()]
    assert (
        await client.post("/api/templates", json=custom_template(name="forbidden"))
    ).status_code == 403
    assert (await client.delete("/api/templates/" + tid)).status_code == 403
    response = await client.post(
        "/api/templates/" + tid + "/hooks",
        json={
            "name": "My notification",
            "values": {"url": "http://upstream/notify", "credential": "stored-private"},
            "enabled": True,
        },
    )
    assert response.status_code == 201, response.text
    hook = response.json()
    assert hook["token"] and hook["hook"]["input_path"].startswith("/hooks/")
    assert hook["hook"]["target"]["headers"][0]["secret"]
    assert "stored-private" not in response.text
    row = await client.app.state.db.one("SELECT config_json FROM hooks WHERE id=?", (hook["id"],))
    assert "stored-private" not in row["config_json"]
    assert (await client.get("/api/hooks/" + hook["id"])).status_code == 200
    assert (await client.get("/app/templates")).status_code == 403


async def test_template_updates_do_not_change_existing_hooks(admin_client):
    created = (await admin_client.post("/api/templates", json=custom_template())).json()
    tid = created["id"]
    hook = (
        await admin_client.post(
            "/api/templates/" + tid + "/hooks",
            json={
                "name": "Old",
                "values": {"url": "http://upstream/old", "credential": "old-private"},
            },
        )
    ).json()
    replacement = custom_template(name="New definition", enabled=False)
    assert (await admin_client.put("/api/templates/" + tid, json=replacement)).status_code == 200
    assert (await admin_client.get("/api/hooks/" + hook["id"])).json()["target"][
        "url"
    ] == "http://upstream/old"
    assert (
        await admin_client.post(
            "/api/templates/" + tid + "/hooks",
            json={"name": "Disabled", "values": {"url": "http://upstream/", "credential": "x"}},
        )
    ).status_code == 404
    assert (await admin_client.delete("/api/templates/" + tid)).status_code == 204
    assert (await admin_client.get("/api/hooks/" + hook["id"])).status_code == 200


@pytest.mark.parametrize(
    "changes",
    [
        {
            "fields": [
                {"key": "token", "label": "Token", "kind": "secret", "default": "do-not-store"}
            ]
        },
        {"target": {"method": "POST", "url": "http://{{inputs.unknown}}/"}},
        {"target": {"method": "POST", "url": '{{__import__("os")}}'}},
        {"target": {"method": "POST", "url": "{{inputs.url}}/{{inputs.credential}}"}},
        {
            "target": {
                "method": "POST",
                "url": "{{inputs.url}}",
                "headers": [{"name": "Authorization", "value": "literal-private"}],
            }
        },
    ],
)
async def test_invalid_or_secret_template_definitions_are_rejected(admin_client, changes):
    response = await admin_client.post("/api/templates", json=custom_template(**changes))
    assert response.status_code == 400, response.text


@pytest.mark.parametrize(
    "values",
    [
        {"url": "file:///tmp/private", "credential": "x"},
        {"url": "http://user:pass@upstream/", "credential": "x"},
        {"url": "http://upstream/"},
        {"url": "http://upstream/", "credential": "x", "unexpected": "value"},
    ],
)
async def test_template_values_are_validated_before_creating_hook(admin_client, values):
    tid = (await admin_client.post("/api/templates", json=custom_template())).json()["id"]
    assert (
        await admin_client.post(
            "/api/templates/" + tid + "/hooks", json={"name": "Invalid", "values": values}
        )
    ).status_code == 400
    assert not await admin_client.app.state.db.one("SELECT * FROM hooks")


async def test_seed_runs_once_and_csrf_is_required(admin_client):
    assert (await admin_client.delete("/api/templates/simple-http-url")).status_code == 204
    from hookrouty.wizard import seed_templates

    await seed_templates(admin_client.app.state.db)
    assert (await admin_client.get("/api/templates/simple-http-url")).status_code == 404
    old = admin_client.headers.pop("X-CSRF-Token")
    assert (await admin_client.post("/api/templates", json=custom_template())).status_code == 403
    admin_client.headers["X-CSRF-Token"] = old


async def test_database_v1_upgrades_without_losing_data(tmp_path):
    import base64

    from hookrouty.app import create_app
    from hookrouty.config import Settings

    path = tmp_path / "hookrouty.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY);"
            + Path("src/hookrouty/migrations/0001_initial.sql").read_text(encoding="utf-8")
            + "INSERT INTO schema_migrations VALUES(1);"
        )
        db.execute("INSERT INTO app_meta VALUES('preserve','existing-data')")
    app = create_app(
        Settings(
            app_secret_key=base64.urlsafe_b64encode(b"a" * 32).decode(),
            admin_password="initial-password",
            data_dir=tmp_path,
        )
    )
    async with app.router.lifespan_context(app):
        assert (await app.state.db.one("SELECT value FROM app_meta WHERE key='preserve'"))[
            "value"
        ] == "existing-data"
        assert await app.state.db.one("SELECT version FROM schema_migrations WHERE version=2")
        assert len(await app.state.db.all("SELECT * FROM hook_templates")) == 2


async def test_json_input_keeps_nested_json_types(admin_client):
    definition = {
        "name": "JSON payload",
        "fields": [
            {"key": "url", "label": "URL", "kind": "url"},
            {"key": "payload", "label": "Payload", "kind": "json"},
        ],
        "target": {
            "method": "POST",
            "url": "{{inputs.url}}",
            "body": {"mode": "json", "template": "{{inputs.payload}}"},
        },
    }
    response = await admin_client.post("/api/templates", json=definition)
    assert response.status_code == 201, response.text
    tid = response.json()["id"]
    payload = {"event": {"enabled": True, "version": 2, "items": [1, False]}}
    response = await admin_client.post(
        "/api/templates/" + tid + "/preview",
        json={"name": "Nested JSON", "values": {"url": "http://upstream/", "payload": payload}},
    )
    assert response.status_code == 200, response.text
    assert json.loads(response.json()["body"]["text"]) == payload


async def test_template_cannot_store_literal_sensitive_body_fields(admin_client):
    definition = custom_template(
        target={
            "method": "POST",
            "url": "{{inputs.url}}",
            "body": {"mode": "json", "template": {"password": "literal-private"}},
        }
    )
    assert (await admin_client.post("/api/templates", json=definition)).status_code == 400


@pytest.mark.parametrize("source", ["literal", "default", "input"])
async def test_credentials_cannot_be_supplied_in_url_query(admin_client, source):
    definition = custom_template()
    credential_url = "http://upstream/?%74oken=example-private"
    if source == "literal":
        definition["target"]["url"] = credential_url
    elif source == "default":
        definition["fields"][0]["default"] = credential_url
    response = await admin_client.post("/api/templates", json=definition)
    if source != "input":
        assert response.status_code == 400, response.text
        return
    assert response.status_code == 201
    response = await admin_client.post(
        "/api/templates/" + response.json()["id"] + "/hooks",
        json={"name": "URL credential", "values": {"url": credential_url, "credential": "x"}},
    )
    assert response.status_code == 400, response.text


@pytest.mark.parametrize("kind", ["text", "json"])
async def test_secret_body_cannot_publish_defaults(admin_client, kind):
    definition = {
        "name": "Secret body",
        "fields": [{"key": "value", "label": "Credential", "kind": kind, "default": "private"}],
        "target": {
            "url": "http://upstream/",
            "body": {"mode": "text", "template": "{{inputs.value}}", "secret": True},
        },
    }
    response = await admin_client.post("/api/templates", json=definition)
    assert response.status_code == 400, response.text


async def test_secret_json_body_is_encrypted_and_optional_auth_is_omitted(admin_client):
    response = await admin_client.post(
        "/api/templates/simple-http-url/hooks",
        json={"name": "No auth", "values": {"url": "http://upstream/"}},
    )
    assert response.status_code == 201, response.text
    assert response.json()["hook"]["target"]["headers"] == []
    definition = {
        "name": "Private JSON",
        "fields": [{"key": "payload", "label": "Payload", "kind": "json"}],
        "target": {
            "url": "http://upstream/",
            "body": {"mode": "json", "template": "{{inputs.payload}}", "secret": True},
        },
    }
    response = await admin_client.post("/api/templates", json=definition)
    assert response.status_code == 201, response.text
    tid = response.json()["id"]
    draft = {"name": "Private request", "values": {"payload": {"credential": "json-private"}}}
    preview = await admin_client.post("/api/templates/" + tid + "/preview", json=draft)
    assert preview.status_code == 200 and "json-private" not in preview.text
    created = await admin_client.post("/api/templates/" + tid + "/hooks", json=draft)
    assert created.status_code == 201 and "json-private" not in created.text
    row = await admin_client.app.state.db.one(
        "SELECT config_json FROM hooks WHERE id=?", (created.json()["id"],)
    )
    assert "json-private" not in row["config_json"]


async def test_non_json_default_requires_text(admin_client):
    definition = custom_template()
    definition["fields"][0]["default"] = {"invalid": True}
    response = await admin_client.post("/api/templates", json=definition)
    assert response.status_code == 400, response.text


async def test_member_cannot_access_disabled_templates_or_other_users_hooks(admin_client):
    client = admin_client
    definition = custom_template(enabled=False)
    tid = (await client.post("/api/templates", json=definition)).json()["id"]
    await client.post("/api/users", json={"username": "outsider", "password": "outsider-password"})
    hook = (
        await client.post(
            "/api/templates/simple-http-url/hooks",
            json={"name": "Admin hook", "values": {"url": "http://upstream/", "method": "POST"}},
        )
    ).json()
    await client.post("/api/logout")
    await login(client, "outsider", "outsider-password")
    assert tid not in [t["id"] for t in (await client.get("/api/templates")).json()]
    assert (await client.get("/api/templates/" + tid)).status_code == 404
    assert (
        await client.post(
            "/api/templates/" + tid + "/preview", json={"name": "Hidden", "values": {}}
        )
    ).status_code == 404
    assert (await client.get("/api/hooks/" + hook["id"])).status_code == 404
