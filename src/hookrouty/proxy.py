import asyncio
import contextlib
import logging
import time

import httpx

from .hooks import decode_config
from .models import FORBIDDEN_HEADERS, ProxyReply, RouterError
from .redaction import capture, known_secrets, rendered_snapshot, safe_headers, scrub_known
from .templating import build_context, render_request

log = logging.getLogger("hookrouty")


def filtered_headers(headers):
    connection = set()
    for key, value in headers:
        if key.lower() == "connection":
            connection.update(p.strip().lower() for p in value.split(","))
    forbidden = (
        FORBIDDEN_HEADERS
        | connection
        | {"set-cookie", "content-length", "x-hookrouty-request-id", "x-hookrouty-history-status"}
    )
    return {k: v for k, v in headers if k.lower() not in forbidden}


class ProxyService:
    def __init__(self, state):
        self.state = state
        self.active = {}
        self.closing = False

    async def reject(self, hook, incoming, error, source="webhook"):
        try:
            await self.state.history.start(
                incoming.request_id,
                hook,
                {"error": {"code": error.code, "message": error.public_message}},
                source,
                False,
                "rejected",
            )
        except Exception:
            log.error("history_unavailable request_id=%s", incoming.request_id)

    def render(self, spec, incoming):
        if len(incoming.body) > self.state.settings.max_request_body_bytes:
            raise RouterError(
                413, "PAYLOAD_TOO_LARGE", "Incoming body exceeds the configured limit."
            )
        context = build_context(incoming, incoming.path, set(spec.secret_refs))
        rendered = render_request(spec, context, incoming.body, context.headers.get("content-type"))
        if len(rendered.body) > self.state.settings.max_rendered_body_bytes:
            raise RouterError(
                413, "PAYLOAD_TOO_LARGE", "Rendered body exceeds the configured limit."
            )
        return rendered, context

    async def execute(self, hook, incoming, disconnect=None, source="webhook"):
        rid = incoming.request_id
        spec = decode_config(hook, self.state.box)
        try:
            rendered, context = self.render(spec, incoming)
            if self.closing or len(self.active) >= self.state.settings.max_inflight_requests:
                raise RouterError(
                    503, "CAPACITY_EXCEEDED", "All destination request slots are occupied."
                )
        except RouterError as error:
            await self.reject(hook, incoming, error, source)
            raise
        secrets = known_secrets(context, rendered, spec)
        self.active[rid] = asyncio.current_task()
        start = time.monotonic()
        snapshot = {
            "target": rendered_snapshot(
                rendered, spec, self.state.settings.history_capture_bytes, known_values=secrets
            ),
            "incoming": {
                "path": {
                    k: "[REDACTED]" if "path." + k in context.secret_refs else v
                    for k, v in incoming.path.items()
                },
                "query": {
                    k: "[REDACTED]" if "query." + k in context.secret_refs else v
                    for k, v in context.query.items()
                },
                "headers": safe_headers(incoming.headers, context.secret_refs),
                "body": capture(
                    incoming.body,
                    context.headers.get("content-type"),
                    spec.capture_bodies,
                    context.secret_refs,
                    limit=self.state.settings.history_capture_bytes,
                    known_values=secrets,
                ),
            },
        }
        outcome, status, reply = "interrupted", None, None
        network_task = disconnect_task = None
        recorded = False
        try:
            current = await self.state.db.one(
                "SELECT enabled,deleted_at FROM hooks WHERE id=?", (hook["id"],)
            )
            if (
                not current
                or current["deleted_at"]
                or (source == "webhook" and not current["enabled"])
            ):
                raise RouterError(404, "HOOK_NOT_FOUND", "Hook not found.")
            try:
                await self.state.history.start(rid, hook, scrub_known(snapshot, secrets), source)
                recorded = True
            except Exception:
                raise RouterError(
                    503,
                    "STORAGE_UNAVAILABLE",
                    "History storage is unavailable; destination was not contacted.",
                ) from None
            network_task = asyncio.create_task(self.call(rendered, spec.timeout_seconds))
            if disconnect is not None:
                disconnect_task = asyncio.create_task(
                    disconnect() if callable(disconnect) else disconnect
                )
                done, _ = await asyncio.wait(
                    [network_task, disconnect_task], return_when=asyncio.FIRST_COMPLETED
                )
                if disconnect_task in done and network_task not in done:
                    outcome = "client_disconnected"
                    raise RouterError(
                        499,
                        "CLIENT_DISCONNECTED",
                        "Client disconnected; the remote action may have completed.",
                    )
            reply = await network_task
            status = reply.status_code
            outcome = (
                "success"
                if 200 <= status < 300
                else "redirected"
                if 300 <= status < 400
                else "upstream_error"
            )
            response_headers = {k.lower(): v for k, v in reply.headers.items()}
            snapshot["response"] = {
                "headers": safe_headers(reply.headers.items()),
                "body": capture(
                    reply.body,
                    response_headers.get("content-type"),
                    spec.capture_bodies,
                    context.secret_refs,
                    limit=self.state.settings.history_capture_bytes,
                    known_values=secrets,
                    encoded="content-encoding" in response_headers,
                ),
            }
        except TimeoutError:
            outcome = "timeout"
            raise RouterError(
                504,
                "UPSTREAM_TIMEOUT",
                "Destination did not respond before the timeout; the remote action may have completed.",
            ) from None
        except httpx.HTTPError:
            outcome = "proxy_error"
            raise RouterError(
                502,
                "UPSTREAM_CONNECTION_ERROR",
                "Destination connection failed; the remote action may have completed.",
            ) from None
        except RouterError as error:
            if outcome != "client_disconnected":
                outcome = "proxy_error" if recorded else "rejected"
            snapshot["error"] = {"code": error.code, "message": error.public_message}
            raise
        except asyncio.CancelledError:
            outcome = "interrupted"
            raise
        finally:
            for task in (network_task, disconnect_task):
                if task and not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
            if recorded:
                try:
                    await self.state.history.finish(
                        rid,
                        outcome,
                        status,
                        (time.monotonic() - start) * 1000,
                        scrub_known(snapshot, secrets),
                    )
                except Exception:
                    log.error("history_unavailable request_id=%s outcome=%s", rid, outcome)
                    if reply:
                        reply.headers["X-Hookrouty-History-Status"] = "unavailable"
            self.active.pop(rid, None)
            log.info(
                "execution request_id=%s outcome=%s duration_ms=%.1f",
                rid,
                outcome,
                (time.monotonic() - start) * 1000,
            )
        return reply

    async def call(self, rendered, deadline):
        async with asyncio.timeout(deadline):
            request = self.state.http.build_request(
                rendered.method, rendered.url, headers=rendered.headers, content=rendered.body
            )
            # Do not persist upstream cookies across hooks or callers.
            request.headers.pop("cookie", None) if not any(
                k.lower() == "cookie" for k, v in rendered.headers
            ) else None
            response = await self.state.http.send(request, stream=True, follow_redirects=False)
            try:
                body = bytearray()
                if response.is_stream_consumed:
                    chunks = [response.content]
                    for chunk in chunks:
                        body.extend(chunk)
                else:
                    async for chunk in response.aiter_raw():
                        body.extend(chunk)
                        if len(body) > self.state.settings.max_upstream_response_bytes:
                            raise RouterError(
                                502,
                                "UPSTREAM_RESPONSE_TOO_LARGE",
                                "Destination response exceeds the configured limit.",
                            )
                if len(body) > self.state.settings.max_upstream_response_bytes:
                    raise RouterError(
                        502,
                        "UPSTREAM_RESPONSE_TOO_LARGE",
                        "Destination response exceeds the configured limit.",
                    )
                if rendered.method == "HEAD" or response.status_code in {204, 304}:
                    body = b""
                return ProxyReply(
                    response.status_code,
                    filtered_headers(response.headers.multi_items()),
                    bytes(body),
                )
            finally:
                await response.aclose()
                self.state.http.cookies.clear()

    async def close(self, grace_seconds):
        self.closing = True
        tasks = [task for task in self.active.values() if task is not asyncio.current_task()]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=grace_seconds)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
