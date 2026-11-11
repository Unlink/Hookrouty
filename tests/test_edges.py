import asyncio
import base64
import json
from uuid import uuid4

import httpx
import pytest
from conftest import login
from test_hooks import hook_spec
from test_proxy import configure

from hookrouty.app import create_app
from hookrouty.config import Settings
from hookrouty.models import IncomingRequest, RouterError


async def test_session_expiry_and_wrong_key(client, tmp_path):
    await login(client)
    await client.app.state.db.execute(
        "UPDATE sessions SET expires_at='2000-01-01T00:00:00.000000Z'"
    )
    assert (await client.get("/api/users")).status_code == 401
    app = create_app(
        Settings(app_secret_key=base64.urlsafe_b64encode(b"z" * 32).decode(), data_dir=tmp_path)
    )
    with pytest.raises(RuntimeError, match="does not match"):
        async with app.router.lifespan_context(app):
            pass


async def test_storage_failures_never_retry(admin_client, monkeypatch):
    client = admin_client
    created = await configure(client)
    calls = []
    await client.app.state.http.aclose()
    client.app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: calls.append(r) or httpx.Response(200, content=b"ok")
        )
    )

    async def broken(*args, **kwargs):
        raise OSError("secret-exception-text")

    original = client.app.state.history.start
    monkeypatch.setattr(client.app.state.history, "start", broken)
    response = await client.post("/hooks/call/a", headers={"X-Webhook-Token": created["token"]})
    assert response.status_code == 503
    assert not calls
    assert "secret-exception-text" not in response.text
    monkeypatch.setattr(client.app.state.history, "start", original)
    monkeypatch.setattr(client.app.state.history, "finish", broken)
    response = await client.post("/hooks/call/a", headers={"X-Webhook-Token": created["token"]})
    assert response.status_code == 200 and response.content == b"ok"
    assert response.headers["x-hookrouty-history-status"] == "unavailable"
    assert len(calls) == 1
    await client.app.state.history.prune()
    record = await client.app.state.db.one("SELECT outcome FROM executions")
    assert record["outcome"] == "interrupted"


async def test_disconnect_marks_uncertainty_and_releases_capacity(admin_client):
    client = admin_client
    created = await configure(client)
    hook = await client.app.state.db.one("SELECT * FROM hooks WHERE id=?", (created["id"],))
    started = asyncio.Event()

    async def slow(request):
        started.set()
        await asyncio.sleep(20)
        return httpx.Response(200)

    await client.app.state.http.aclose()
    client.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(slow))
    rid = str(uuid4())
    incoming = IncomingRequest(rid, "POST", b"/hooks/call/a", [], [], b"", {"project": "a"})

    async def disconnect():
        await started.wait()

    with pytest.raises(RouterError) as error:
        await client.app.state.proxy.execute(hook, incoming, disconnect)
    assert error.value.status_code == 499
    assert not client.app.state.proxy.active
    assert (
        await client.app.state.db.one("SELECT outcome FROM executions WHERE request_id=?", (rid,))
    )["outcome"] == "client_disconnected"


async def test_taint_case_insensitivity_and_response_echo(admin_client):
    client = admin_client
    created = await configure(
        client,
        capture_bodies=True,
        secret_refs=["headers.X-Private"],
        target={
            "url": "http://upstream/{{headers.X-Private}}",
            "headers": [{"name": "Authorization", "value": "Bearer stored-secret", "secret": True}],
        },
    )
    await client.app.state.http.aclose()
    client.app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, headers={"X-Echo": "sensitive-value"}, json={"echo": "Bearer stored-secret"}
            )
        )
    )
    response = await client.post(
        "/hooks/call/a",
        headers={"X-Webhook-Token": created["token"], "X-Private": "sensitive-value"},
    )
    assert response.status_code == 200
    records = await client.app.state.db.all("SELECT safe_snapshot FROM executions")
    serialized = json.dumps(records)
    assert "sensitive-value" not in serialized
    assert "stored-secret" not in serialized


async def test_isolation_history_stats_and_revocation(admin_client):
    client = admin_client
    hook = (await client.post("/api/hooks", json=hook_spec())).json()
    (
        await client.post("/api/users", json={"username": "member", "password": "member-password"})
    ).json()
    await client.post("/hooks/deploy/a")  # local rejection
    await client.post("/api/logout")
    await login(client, "member", "member-password")
    assert (await client.get("/api/executions")).json()["total"] == 0
    assert (await client.get("/api/stats")).json()["calls"] == 0
    assert (await client.get("/api/stats?hook_id=" + hook["id"])).status_code == 404
    own = (await client.post("/api/hooks", json=hook_spec("/hooks/own/:project"))).json()
    assert (await client.get("/api/hooks/" + own["id"])).status_code == 200
    await client.post("/api/logout")
    await login(client)
    await client.put("/api/hooks/" + own["id"] + "/members", json={"user_ids": []})
    await client.post("/api/logout")
    await login(client, "member", "member-password")
    assert (await client.get("/api/hooks/" + own["id"])).status_code == 404


async def test_retention_does_not_remove_running(admin_client):
    client = admin_client
    created = await configure(client)
    hook = await client.app.state.db.one("SELECT * FROM hooks WHERE id=?", (created["id"],))
    for _ in range(5):
        await client.app.state.history.start(str(uuid4()), hook, {}, "webhook", False, "rejected")
    client.app.state.settings.history_max_records = 2
    await client.app.state.history.prune()
    assert (await client.app.state.db.one("SELECT COUNT(*) AS n FROM executions"))["n"] == 2


async def test_concurrent_last_admin_protection(admin_client):
    client = admin_client
    users = (await client.get("/api/users")).json()
    other = (
        await client.post(
            "/api/users",
            json={"username": "second", "password": "second-password", "role": "admin"},
        )
    ).json()
    from hookrouty.models import Principal, UserPatch
    from hookrouty.users import update_user

    actor = Principal(users[0]["id"], "admin", "admin")
    results = await asyncio.gather(
        *(
            update_user(client.app.state.db, actor, uid, UserPatch(role="user"))
            for uid in [actor.id, other["id"]]
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(r, RouterError) and r.status_code == 409 for r in results) == 1


def test_redaction_before_truncation():
    from hookrouty.redaction import capture

    body = json.dumps({"text": "a" * 20000, "password": "secret-after-limit"}).encode()
    result = capture(body, "application/json", True, limit=16384)
    assert result["truncated"] and "secret-after-limit" not in result["text"]


async def test_secret_query_echo_is_redacted(admin_client):
    client = admin_client
    created = await configure(
        client,
        capture_bodies=True,
        target={
            "url": "http://upstream/",
            "query": [{"name": "credential", "value": "query-secret", "secret": True}],
        },
    )
    await client.app.state.http.aclose()
    client.app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, headers={"X-Echo": "query-secret"}, json={"echo": "query-secret"}
            )
        )
    )
    result = await client.post("/hooks/call/a", headers={"X-Webhook-Token": created["token"]})
    assert result.status_code == 200
    records = await client.app.state.db.all("SELECT safe_snapshot FROM executions")
    assert "query-secret" not in json.dumps(records)


async def test_manual_test_receives_disconnect_monitor(admin_client, monkeypatch):
    created = await configure(admin_client)
    from hookrouty.models import ProxyReply

    async def execute(hook, incoming, disconnect=None, source="webhook"):
        assert callable(disconnect), "Manual test must monitor the caller disconnect"
        assert source == "manual_test"
        return ProxyReply(200, {}, b"ok")

    monkeypatch.setattr(admin_client.app.state.proxy, "execute", execute)
    response = await admin_client.post(
        "/api/hooks/" + created["id"] + "/test", json={"path": {"project": "a"}}
    )
    assert response.status_code == 200


def test_dot_segments_cannot_escape_target_path():
    from hookrouty.models import HookSpec, TemplateContext
    from hookrouty.templating import render_request

    spec = HookSpec(
        name="dots", input_path="/hooks/:p", target={"url": "http://upstream/fixed/{{path.p}}/run"}
    )
    for value in [".", ".."]:
        rendered = render_request(
            spec, TemplateContext({"p": value}, {}, {}, None, set()), b"", None
        )
        assert str(httpx.URL(rendered.url)).startswith("http://upstream/fixed/%2E")
        assert str(httpx.URL(rendered.url)).endswith("/run")


@pytest.mark.parametrize("status", [204, 304])
async def test_bodyless_statuses(admin_client, status):
    created = await configure(admin_client)
    await admin_client.app.state.http.aclose()
    admin_client.app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(status, content=b"discard-me"))
    )
    response = await admin_client.post(
        "/hooks/call/a", headers={"X-Webhook-Token": created["token"]}
    )
    assert response.status_code == status and response.content == b""


async def test_head_and_binary_response(admin_client):
    created = await configure(
        admin_client, input_method="HEAD", target={"method": "HEAD", "url": "http://upstream/"}
    )
    await admin_client.app.state.http.aclose()
    admin_client.app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"discard"))
    )
    response = await admin_client.head(
        "/hooks/call/a", headers={"X-Webhook-Token": created["token"]}
    )
    assert response.status_code == 200 and response.content == b""
    rejected = await admin_client.head("/hooks/call/a")
    assert (
        rejected.status_code == 401
        and rejected.content == b""
        and rejected.headers["x-hookrouty-request-id"]
    )
    await admin_client.patch(
        "/api/hooks/" + created["id"], json={"input_method": "POST", "target": {"method": "POST"}}
    )
    await admin_client.app.state.http.aclose()
    payload = b"\xff\x00\xfe\x01"
    admin_client.app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, headers={"Content-Type": "application/octet-stream"}, content=payload
            )
        )
    )
    response = await admin_client.post(
        "/hooks/call/a", headers={"X-Webhook-Token": created["token"]}
    )
    assert response.content == payload


async def test_query_auth_static_priority_and_disabled(admin_client):
    general = await configure(admin_client, input_path="/hooks/:project", auth={"mode": "query"})
    specific = await configure(admin_client, input_path="/hooks/fixed")
    # The fixed route never falls back to an authenticated parameter route.
    assert (await admin_client.post("/hooks/fixed?token=" + general["token"])).status_code == 401
    assert (
        await admin_client.post("/hooks/other", headers={"X-Webhook-Token": general["token"]})
    ).status_code == 401
    await admin_client.patch("/api/hooks/" + specific["id"], json={"enabled": False})
    await admin_client.patch("/api/hooks/" + general["id"], json={"enabled": False})
    assert (await admin_client.post("/hooks/other?token=" + general["token"])).status_code == 404


async def test_secrets_are_encrypted_and_explicitly_editable(admin_client):
    created = await configure(
        admin_client,
        target={
            "url": "http://upstream/",
            "query": [{"name": "credential", "value": "encrypted-query-value", "secret": True}],
            "body": {"mode": "json", "template": {"value": "encrypted-body-value"}, "secret": True},
        },
    )
    hid = created["id"]
    stored = await admin_client.app.state.db.one("SELECT config_json FROM hooks WHERE id=?", (hid,))
    assert (
        "encrypted-query-value" not in stored["config_json"]
        and "encrypted-body-value" not in stored["config_json"]
    )
    safe = (await admin_client.get("/api/hooks/" + hid)).json()
    assert safe["target"]["body"]["template"] is None
    assert (
        await admin_client.patch("/api/hooks/" + hid, json={"name": "changed"})
    ).status_code == 200
    preview = (await admin_client.post("/api/hooks/" + hid + "/preview", json={})).json()
    assert preview["url"] == "[REDACTED]" and preview["body"]["omitted"] == "secret"
    field = safe["target"]["query"][0]
    field["value_action"] = "remove"
    result = await admin_client.patch(
        "/api/hooks/" + hid, json={"target": {"query": [field], "body": {"value_action": "remove"}}}
    )
    assert result.status_code == 200, result.text
    assert result.json()["target"]["query"] == []
    assert result.json()["target"]["body"]["mode"] == "none"


async def test_rename_role_deactivate_invalidates_session(admin_client):
    user = (
        await admin_client.post(
            "/api/users", json={"username": "member", "password": "member-password"}
        )
    ).json()
    # Independent session sharing the application, without sharing cookie jars.
    from conftest import login

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=admin_client.app), base_url="http://test"
    ) as member:
        assert (await login(member, "member", "member-password")).status_code == 200
        assert (
            await admin_client.patch("/api/users/" + user["id"], json={"active": False})
        ).status_code == 200
        assert (await member.get("/api/hooks")).status_code == 401


@pytest.mark.parametrize(
    "target",
    [
        {"url": "http://{{query.host}}/"},
        {"url": "ftp://server/"},
        {"url": "http://user:pass@server/"},
        {"url": "http://server/", "headers": [{"name": "Host", "value": "evil"}]},
        {
            "url": "http://server/",
            "headers": [{"name": "X-Header", "value": "ok\r\nX-Injected: yes"}],
        },
        {"url": "http://server/", "body": {"mode": "text", "template": "{{__import__('os')}}"}},
        {"url": "http://server/", "method": "GET", "body": {"mode": "passthrough"}},
    ],
)
async def test_configuration_rejects_unsafe_constructs(admin_client, target):
    response = await admin_client.post(
        "/api/hooks", json={"name": "invalid", "input_path": "/hooks/check", "target": target}
    )
    assert response.status_code == 400, response.text


async def test_api_requires_object_body(admin_client):
    for path in ["/api/login", "/api/me/password"]:
        response = await admin_client.post(path, json=[])
        assert response.status_code == 400
