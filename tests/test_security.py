import json
from urllib.parse import quote, quote_plus

import httpx
import pytest
from conftest import login
from test_proxy import configure

from hookrouty.models import HookSpec, TemplateContext
from hookrouty.redaction import capture
from hookrouty.templating import render_request


async def upstream(client, handler=None):
    await client.app.state.http.aclose()
    client.app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler or (lambda r: httpx.Response(200, json={"ok": True})))
    )


async def test_snapshot_exception_always_releases_capacity(admin_client, monkeypatch):
    import hookrouty.proxy as proxy

    created = await configure(admin_client)
    await upstream(admin_client)

    def broken(*args, **kwargs):
        raise RuntimeError("snapshot failure")

    with monkeypatch.context() as patch:
        patch.setattr(proxy, "rendered_snapshot", broken)
        with pytest.raises(RuntimeError, match="snapshot failure"):
            await admin_client.post("/hooks/call/a", headers={"X-Webhook-Token": created["token"]})
    assert not admin_client.app.state.proxy.active
    assert (
        await admin_client.post("/hooks/call/a", headers={"X-Webhook-Token": created["token"]})
    ).status_code == 200


@pytest.mark.parametrize("body", [b'{"value":"\\ud800"}', b"[" * 1200 + b"0" + b"]" * 1200])
async def test_invalid_capture_does_not_poison_proxy(admin_client, body):
    await upstream(admin_client)
    created = await configure(admin_client, capture_bodies=True)
    headers = {"X-Webhook-Token": created["token"], "Content-Type": "application/json"}
    assert (
        await admin_client.post("/hooks/call/a", headers=headers, content=body)
    ).status_code == 200
    assert not admin_client.app.state.proxy.active
    assert (await admin_client.post("/hooks/call/a", headers=headers, json={})).status_code == 200
    rows = await admin_client.app.state.db.all("SELECT safe_snapshot FROM executions")
    assert "invalid_text" in rows[0]["safe_snapshot"]


@pytest.mark.parametrize("authenticated", [False, True])
async def test_management_stream_limit_stops_consumption(client, authenticated):
    if authenticated:
        await login(client)
    client.app.state.settings.max_management_body_bytes = 128
    consumed = 0

    async def chunks():
        nonlocal consumed
        for chunk in [b'{"name":"', b"a" * 128, b"a" * 128, b'"}']:
            consumed += len(chunk)
            yield chunk

    response = await client.post(
        "/api/hooks", headers={"Content-Type": "application/json"}, content=chunks()
    )
    assert response.status_code == 413
    assert consumed < 266
    assert response.json()["request_id"] == response.headers["x-hookrouty-request-id"]


async def test_management_declared_size_and_exact_boundary(admin_client):
    admin_client.app.state.settings.max_management_body_bytes = 64
    consumed = False

    async def body():
        nonlocal consumed
        consumed = True
        yield b"{}"

    response = await admin_client.post(
        "/api/hooks", headers={"Content-Length": "65"}, content=body()
    )
    assert response.status_code == 413 and not consumed
    # A legal boundary-length JSON body reaches application validation.
    response = await admin_client.post(
        "/api/hooks", content=b"{}" + b" " * 62, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400


async def test_management_slow_body_deadline(client, monkeypatch):
    import asyncio

    import hookrouty.middleware as middleware

    monkeypatch.setattr(middleware, "MANAGEMENT_BODY_TIMEOUT_SECONDS", 0.01)

    async def body():
        yield b"{"
        await asyncio.sleep(0.1)
        yield b"}"

    result = await client.post("/api/hooks", content=body())
    assert result.status_code == 408


async def test_nested_array_secrets_propagate_to_preview_and_history(admin_client):
    secret = "nested-sensitive-value"
    await upstream(
        admin_client,
        lambda r: httpx.Response(200, headers={"X-Echo": secret}, json={"echo": secret}),
    )
    created = await configure(
        admin_client,
        capture_bodies=True,
        target={
            "url": "http://upstream/",
            "body": {"mode": "json", "template": {"data": "{{body.items}}"}},
        },
    )
    sample = {
        "body": json.dumps({"items": [[{"password": secret}]]}),
        "headers": [["Content-Type", "application/json"]],
    }
    preview = await admin_client.post("/api/hooks/" + created["id"] + "/preview", json=sample)
    assert preview.status_code == 200
    assert preview.json()["body"]["omitted"] == "secret"
    response = await admin_client.post(
        "/hooks/call/a",
        headers={"X-Webhook-Token": created["token"], "Content-Type": "application/json"},
        content=sample["body"],
    )
    assert response.status_code == 200
    rows = await admin_client.app.state.db.all("SELECT safe_snapshot FROM executions")
    assert secret not in json.dumps(rows)


@pytest.mark.parametrize(
    "encoded", [lambda x: x, lambda x: quote(x, safe=""), lambda x: quote_plus(x, safe="")]
)
async def test_encoded_query_echo_is_masked_before_capture_limit(admin_client, encoded):
    secret = "two words/+?"
    echo = encoded(secret)
    await upstream(
        admin_client, lambda r: httpx.Response(200, headers={"X-Echo": echo}, json={"echo": echo})
    )
    created = await configure(
        admin_client,
        capture_bodies=True,
        target={
            "url": "http://upstream/",
            "query": [{"name": "credential", "value": secret, "secret": True}],
        },
    )
    response = await admin_client.post(
        "/hooks/call/a", headers={"X-Webhook-Token": created["token"]}
    )
    assert response.status_code == 200
    rows = await admin_client.app.state.db.all("SELECT safe_snapshot FROM executions")
    assert echo not in json.dumps(rows)
    captured = capture(
        json.dumps({"echo": echo}).encode(),
        "application/json",
        True,
        known_values={secret},
        limit=12,
    )
    assert not any(
        fragment in captured["text"] for fragment in ["two words", "two+words", "two%20words"]
    )


@pytest.mark.parametrize(
    "template,query",
    [
        ("..{{query.value}}", {"value": ""}),
        (".{{query.value}}", {"value": "."}),
        ("{{query.a}}{{query.b}}", {"a": ".", "b": "."}),
        (".{{query.value}}", {"value": ""}),
    ],
)
def test_completed_path_segments_remain_confined(template, query):
    spec = HookSpec(
        name="path",
        input_path="/hooks/path",
        target={"url": "http://upstream/fixed/" + template + "/run"},
    )
    rendered = render_request(spec, TemplateContext({}, query, {}, None, set()), b"", None)
    url = httpx.URL(rendered.url)
    assert url.raw_path.startswith(b"/fixed/") and b"%2E" in url.raw_path
    assert url.raw_path.endswith(b"/run")


async def test_hook_documents_enforce_sandbox_and_remove_reporting(admin_client):
    payload = b"<html>ordinary upstream bytes</html>"
    await upstream(
        admin_client,
        lambda r: httpx.Response(
            200,
            content=payload,
            headers={
                "Content-Type": "text/html",
                "Content-Security-Policy": "script-src * 'unsafe-inline'",
                "Content-Security-Policy-Report-Only": "report-uri https://example.test/report",
                "Reporting-Endpoints": 'default="https://example.test/report"',
            },
        ),
    )
    created = await configure(admin_client)
    result = await admin_client.post("/hooks/call/a", headers={"X-Webhook-Token": created["token"]})
    assert result.content == payload and result.status_code == 200
    policy = result.headers["content-security-policy"]
    assert "sandbox" in policy and "default-src 'none'" in policy
    assert "allow-scripts" not in policy and "unsafe-inline" not in policy
    assert "content-security-policy-report-only" not in result.headers
    assert "reporting-endpoints" not in result.headers


async def test_anonymous_session_budget_is_atomic_and_reclaims_expired(client, monkeypatch):
    import asyncio

    import hookrouty.auth as auth

    monkeypatch.setattr(auth, "MAX_ANONYMOUS_SESSIONS", 2)

    async def anonymous():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app), base_url="http://test"
        ) as other:
            return await other.get("/api/session")

    responses = await asyncio.gather(*(anonymous() for _ in range(5)))
    assert sorted(r.status_code for r in responses) == [200, 200, 429, 429, 429]
    assert (
        await client.app.state.db.one("SELECT COUNT(*) AS n FROM sessions WHERE user_id IS NULL")
    )["n"] == 2
    await client.app.state.db.execute(
        "UPDATE sessions SET expires_at='2000-01-01T00:00:00.000000Z'"
    )
    assert (await anonymous()).status_code == 200


async def test_management_actual_bytes_override_misleading_length(client):
    client.app.state.settings.max_management_body_bytes = 32
    result = await client.post("/api/hooks", headers={"Content-Length": "1"}, content=b"a" * 33)
    assert result.status_code == 413


async def test_management_huge_declared_length_is_safely_rejected(client):
    result = await client.post("/api/hooks", headers={"Content-Length": "9" * 5000}, content=b"{}")
    assert result.status_code == 413


async def test_deep_management_json_has_safe_error(client):
    result = await client.post(
        "/api/hooks",
        headers={"Content-Type": "application/json"},
        content=b"[" * 3000 + b"0" + b"]" * 3000,
    )
    assert result.status_code == 400 and result.json()["error"]["code"] == "INVALID_INPUT"


async def test_numeric_secret_inside_array_is_masked(admin_client):
    value = 92837465
    await upstream(admin_client, lambda r: httpx.Response(200, json={"echo": value}))
    created = await configure(admin_client, capture_bodies=True)
    result = await admin_client.post(
        "/hooks/call/a",
        headers={"X-Webhook-Token": created["token"], "Content-Type": "application/json"},
        json={"items": [{"password": value}]},
    )
    assert result.status_code == 200
    records = await admin_client.app.state.db.all("SELECT safe_snapshot FROM executions")
    assert str(value) not in json.dumps(records)
