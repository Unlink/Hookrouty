import asyncio
import json

import httpx
import pytest
from test_hooks import hook_spec


async def configure(client, **changes):
    spec = hook_spec("/hooks/call/:project")
    spec["target"] = {"url": "http://upstream/{{path.project}}", "body": {"mode": "passthrough"}}
    spec.update(changes)
    response = await client.post("/api/hooks", json=spec)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("status", [200, 302, 400, 500])
async def test_forward_one_attempt_and_headers(admin_client, status):
    client = admin_client
    calls = []

    async def handler(request):
        calls.append(request)
        assert request.content == b"raw-body"
        assert "x-webhook-token" not in request.headers
        assert "authorization" not in request.headers
        return httpx.Response(
            status,
            headers={
                "Location": "http://elsewhere/",
                "Set-Cookie": "bad=1",
                "Connection": "x-private",
                "x-private": "hidden",
                "Content-Type": "text/plain",
            },
            content=b"response",
        )

    await client.app.state.http.aclose()
    client.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=None)
    hook = await configure(client)
    result = await client.post(
        "/hooks/call/demo",
        headers={"X-Webhook-Token": hook["token"], "Authorization": "do-not-forward"},
        content=b"raw-body",
    )
    assert result.status_code == status
    assert result.content == b"response"
    assert "set-cookie" not in result.headers and "x-private" not in result.headers
    assert len(calls) == 1
    execution = (await client.get("/api/executions")).json()["items"][0]
    assert execution["request_id"] == result.headers["x-hookrouty-request-id"]
    assert execution["attempted_target"]


async def test_timeout_and_capacity(admin_client):
    client = admin_client
    calls = 0
    started = asyncio.Event()

    async def handler(request):
        nonlocal calls
        calls += 1
        started.set()
        await asyncio.sleep(2)
        return httpx.Response(200, content=b"ok")

    await client.app.state.http.aclose()
    client.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=None)
    client.app.state.settings.max_inflight_requests = 1
    hook = await configure(client, timeout_seconds=1)
    headers = {"X-Webhook-Token": hook["token"]}
    pending = asyncio.create_task(client.post("/hooks/call/a", headers=headers))
    await started.wait()
    assert (await client.post("/hooks/call/a", headers=headers)).status_code == 503
    assert (await pending).status_code == 504
    assert calls == 1


async def test_preview_taint_and_database_redaction(admin_client):
    client = admin_client
    hook = await configure(
        client,
        capture_bodies=True,
        secret_refs=["query.private"],
        target={
            "url": "http://upstream/{{query.private}}",
            "body": {"mode": "json", "template": {"value": "{{body.password}}"}},
        },
    )
    samples = {
        "query": [["private", "ultra-secret"]],
        "headers": [["Content-Type", "application/json"]],
        "body": '{"password":"secret-body"}',
    }
    response = await client.post("/api/hooks/" + hook["id"] + "/preview", json=samples)
    assert response.status_code == 200
    assert "ultra-secret" not in response.text and "secret-body" not in response.text
    assert response.json()["url"] == "[REDACTED]"
    await client.app.state.http.aclose()
    client.app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"password": "response-secret"})
        )
    )
    result = await client.post("/api/hooks/" + hook["id"] + "/test", json=samples)
    assert result.status_code == 200
    rows = await client.app.state.db.all("SELECT safe_snapshot FROM executions")
    assert all(
        secret not in json.dumps(rows)
        for secret in ["ultra-secret", "secret-body", "response-secret"]
    )


async def test_request_and_response_limits(admin_client):
    client = admin_client
    hook = await configure(client)
    client.app.state.settings.max_request_body_bytes = 8
    assert (
        await client.post(
            "/hooks/call/a", headers={"X-Webhook-Token": hook["token"]}, content=b"x" * 9
        )
    ).status_code == 413
    client.app.state.settings.max_upstream_response_bytes = 8
    await client.app.state.http.aclose()
    client.app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 9))
    )
    assert (
        await client.post("/hooks/call/a", headers={"X-Webhook-Token": hook["token"]})
    ).status_code == 502
