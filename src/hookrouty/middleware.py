"""Receive-boundary limits applied before FastAPI deserializes management input."""

import asyncio

from starlette.responses import JSONResponse

MANAGEMENT_BODY_TIMEOUT_SECONDS = 15


class ManagementBodyLimitMiddleware:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not (
            scope["path"] == "/api" or scope["path"].startswith("/api/")
        ):
            return await self.app(scope, receive, send)

        async def reject(status, code, message):
            response = JSONResponse(
                {
                    "error": {"code": code, "message": message},
                    "request_id": scope["state"]["request_id"],
                },
                status_code=status,
            )
            await response(scope, receive, send)

        limit = self.settings.max_management_body_bytes
        lengths = [value for name, value in scope["headers"] if name.lower() == b"content-length"]
        if lengths:
            if len(lengths) != 1 or not lengths[0].isdigit():
                return await reject(400, "INVALID_INPUT", "Invalid Content-Length.")
            declared = lengths[0].lstrip(b"0") or b"0"
            if len(declared) > len(str(limit)) or int(declared) > limit:
                return await reject(
                    413, "PAYLOAD_TOO_LARGE", "Management body exceeds the configured limit."
                )
        body = bytearray()
        try:
            async with asyncio.timeout(MANAGEMENT_BODY_TIMEOUT_SECONDS):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > limit:
                        return await reject(
                            413,
                            "PAYLOAD_TOO_LARGE",
                            "Management body exceeds the configured limit.",
                        )
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            return await reject(
                408, "REQUEST_BODY_TIMEOUT", "Management body was not received before the deadline."
            )
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)
