import asyncio
import contextlib
import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect

from .api import router
from .auth import LoginLimiter, bootstrap_admin
from .config import Settings
from .db import Database
from .history import History
from .middleware import ManagementBodyLimitMiddleware
from .models import IncomingRequest, RouterError
from .proxy import ProxyService
from .routing import compile_route, resolve_route
from .security import SecretBox, now, token_digest


class RequestIdMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        rid = str(uuid4())
        scope.setdefault("state", {})["request_id"] = rid

        async def safe_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() != b"x-hookrouty-request-id"
                ] + [
                    (b"x-hookrouty-request-id", rid.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                ]
                if scope["path"].startswith("/hooks/"):
                    # Upstream documents must never become management-origin code.
                    reporting = {
                        b"content-security-policy",
                        b"content-security-policy-report-only",
                        b"report-to",
                        b"reporting-endpoints",
                        b"nel",
                    }
                    message["headers"] = [
                        (k, v) for k, v in message["headers"] if k.lower() not in reporting
                    ]
                    message["headers"].append(
                        (
                            b"content-security-policy",
                            b"sandbox; default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
                        )
                    )
                else:
                    message["headers"].append((b"cache-control", b"no-store"))
                    message["headers"].append(
                        (
                            b"content-security-policy",
                            b"default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                        )
                    )
            if message["type"] == "http.response.body" and scope["method"] == "HEAD":
                message["body"] = b""
            await send(message)

        await self.app(scope, receive, safe_send)


def create_app(settings: Settings | None = None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        state = app.state
        state.db = await Database.open(settings.data_dir / "hookrouty.sqlite3")
        state.settings, state.box = settings, SecretBox(settings.app_secret_key)
        cleanup = None
        try:
            await state.db.migrate()
            fingerprint = await state.db.one("SELECT value FROM app_meta WHERE key='key_check'")
            if fingerprint and fingerprint["value"] != state.box.fingerprint:
                raise RuntimeError("APP_SECRET_KEY does not match this database")
            await state.db.execute(
                "INSERT OR IGNORE INTO app_meta VALUES('key_check',?)", (state.box.fingerprint,)
            )
            await bootstrap_admin(state.db, settings)
            state.limiter = LoginLimiter()
            state.http = httpx.AsyncClient(
                follow_redirects=False,
                trust_env=False,
                timeout=None,
                transport=httpx.AsyncHTTPTransport(
                    retries=0,
                    limits=httpx.Limits(
                        max_connections=settings.max_inflight_requests,
                        max_keepalive_connections=settings.max_inflight_requests,
                    ),
                ),
            )
            state.history, state.proxy = History(state), ProxyService(state)
            await state.db.execute(
                "UPDATE executions SET outcome='interrupted',finished_at=? WHERE outcome='running'",
                (now(),),
            )

            async def maintain():
                while True:
                    try:
                        await state.history.prune()
                    except Exception:
                        logging.getLogger("hookrouty").error("history_maintenance_unavailable")
                    await asyncio.sleep(300)

            cleanup = asyncio.create_task(maintain())
            yield
        finally:
            if cleanup:
                cleanup.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await cleanup
            if hasattr(state, "proxy"):
                await state.proxy.close(settings.shutdown_grace_seconds)
            if hasattr(state, "http"):
                await state.http.aclose()
            await state.db.close()

    app = FastAPI(
        title="Hookrouty",
        version="0.1.0",
        lifespan=lifespan,
        redirect_slashes=False,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(ManagementBodyLimitMiddleware, settings=settings)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(router)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    @app.exception_handler(RouterError)
    async def router_error(request: Request, error):
        return JSONResponse(
            {
                "error": {"code": error.code, "message": error.public_message},
                "request_id": request.state.request_id,
            },
            status_code=error.status_code,
            headers={"Retry-After": "900"} if error.status_code == 429 else None,
        )

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def invalid(request: Request, error):
        return await router_error(
            request,
            RouterError(
                400,
                "INVALID_INPUT",
                "Invalid request configuration. Check the field types and documented constraints.",
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def framework_error(request: Request, error):
        if error.status_code in {400, 422}:
            return await router_error(request, RouterError(400, "INVALID_INPUT", "Invalid input."))
        return JSONResponse(
            {"detail": error.detail}, status_code=error.status_code, headers=error.headers
        )

    @app.exception_handler(RecursionError)
    @app.exception_handler(ValueError)
    async def bad_value(request: Request, error):
        return await router_error(request, RouterError(400, "INVALID_INPUT", "Invalid input."))

    @app.get("/healthz")
    async def health():
        try:
            await app.state.db.one("SELECT 1")
            return {"status": "ok"}
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)

    @app.get("/")
    async def root():
        return RedirectResponse("/app/hooks", status_code=303)

    @app.api_route(
        "/hooks/{hook_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"],
    )
    async def dispatch(request: Request, hook_path: str):
        state = app.state
        rows = await state.db.all("SELECT * FROM hooks WHERE deleted_at IS NULL AND enabled=1")
        routes = [compile_route(row["input_method"], row["input_path"], row["id"]) for row in rows]
        try:
            match = resolve_route(
                routes, request.method, request.scope.get("raw_path", request.url.path.encode())
            )
        except RouterError as error:
            if error.status_code == 404:
                await state.db.execute(
                    "INSERT INTO aggregate_counters VALUES(?,'unknown_route',1) ON CONFLICT(bucket_start,kind) DO UPDATE SET count=count+1",
                    (now()[:10],),
                )
            raise
        hook = next(r for r in rows if r["id"] == match.hook_id)
        import json

        mode = json.loads(hook["config_json"])["auth"]["mode"]
        incoming = IncomingRequest(
            request.state.request_id,
            request.method,
            request.scope.get("raw_path", b""),
            list(request.query_params.multi_items()),
            list(request.headers.items()),
            b"",
            match.path,
        )
        token = (
            request.headers.get("X-Webhook-Token", "")
            if mode == "header"
            else request.query_params.get("token", "")
        )
        if not token or not hmac.compare_digest(token_digest(token), hook["token_hash"]):
            error = RouterError(401, "INVALID_TOKEN", "Invalid webhook token.")
            await state.proxy.reject(hook, incoming, error)
            raise error
        try:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > settings.max_request_body_bytes:
                    error = RouterError(
                        413, "PAYLOAD_TOO_LARGE", "Incoming body exceeds the configured limit."
                    )
                    await state.proxy.reject(hook, incoming, error)
                    raise error
            incoming.body = bytes(body)
        except ClientDisconnect:
            error = RouterError(
                499,
                "CLIENT_DISCONNECTED",
                "Client disconnected before the destination was contacted.",
            )
            await state.proxy.reject(hook, incoming, error)
            raise error

        async def disconnected():
            while True:
                if (await request.receive())["type"] == "http.disconnect":
                    return

        reply = await state.proxy.execute(hook, incoming, disconnected)
        return Response(
            reply.body if request.method != "HEAD" else b"",
            status_code=reply.status_code,
            headers=reply.headers,
        )

    from .web import router as web_router

    app.include_router(web_router)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    return app
