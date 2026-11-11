import asyncio
import hmac
import time
from collections import defaultdict, deque
from uuid import uuid4

from fastapi import Request

from .models import Principal, RouterError
from .security import future, hash_password, new_token, now, verify_password

COOKIE = "hookrouty_session"


async def session(request: Request):
    token = request.cookies.get(COOKIE, "")
    if not token:
        return None
    return await request.app.state.db.one(
        "SELECT s.*,u.username,u.role,u.active,u.auth_version AS current_version FROM sessions s LEFT JOIN users u ON u.id=s.user_id WHERE s.token_digest=? AND s.expires_at>?",
        (request.app.state.box.session_digest(token), now()),
    )


async def current_principal(request: Request):
    row = await session(request)
    if (
        not row
        or not row["user_id"]
        or not row["active"]
        or row["auth_version"] != row["current_version"]
    ):
        raise RouterError(401, "AUTHENTICATION_REQUIRED", "Sign in to continue.")
    return Principal(row["user_id"], row["username"], row["role"])


async def require_csrf(request):
    row = await session(request)
    token = request.headers.get("X-CSRF-Token", "")
    if not row or not token or not hmac.compare_digest(token, row["csrf_token"]):
        raise RouterError(403, "CSRF_FAILED", "Refresh the page and try again.")


async def new_session(request, response, user=None):
    token, csrf = new_token(), new_token()
    await request.app.state.db.execute(
        "INSERT INTO sessions VALUES(?,?,?,?,?)",
        (
            request.app.state.box.session_digest(token),
            user["id"] if user else None,
            user["auth_version"] if user else None,
            csrf,
            future(86400 if user else 900),
        ),
    )
    response.set_cookie(
        COOKIE,
        token,
        max_age=86400 if user else 900,
        httponly=True,
        samesite="lax",
        secure=request.app.state.settings.app_url.startswith("https://"),
        path="/",
    )
    return {
        "csrf_token": csrf,
        "user": {k: user[k] for k in ("id", "username", "role")} if user else None,
    }


class LoginLimiter:
    def __init__(self):
        self.failures = defaultdict(deque)
        self.lock = asyncio.Lock()
        self.dummy = hash_password(new_token())

    async def authenticate(self, request, username, password):
        keys = [
            "name:" + username.lower(),
            "ip:" + (request.client.host if request.client else "unknown"),
        ]
        async with self.lock:
            current = time.monotonic()
            for key in list(self.failures):
                while self.failures[key] and self.failures[key][0] < current - 900:
                    self.failures[key].popleft()
                if not self.failures[key]:
                    del self.failures[key]
            if any(len(self.failures.get(key, ())) >= 5 for key in keys):
                raise RouterError(429, "LOGIN_LIMIT", "Too many login attempts. Try again later.")
            user = await request.app.state.db.one(
                "SELECT * FROM users WHERE username=?", (username,)
            )
            valid = await asyncio.to_thread(
                verify_password, password, user["password_hash"] if user else self.dummy
            )
            if not user or not user["active"] or not valid:
                for key in keys:
                    self.failures[key].append(current)
                raise RouterError(401, "INVALID_CREDENTIALS", "Invalid username or password.")
            return user


async def bootstrap_admin(db, settings):
    async with db.transaction() as conn:
        async with conn.execute("SELECT id FROM users LIMIT 1") as cursor:
            if await cursor.fetchone():
                return
        if not settings.admin_password:
            raise RuntimeError("ADMIN_PASSWORD is required for an empty database")
        if len(settings.admin_password) < 8:
            raise RuntimeError("ADMIN_PASSWORD must contain at least 8 characters")
        await conn.execute(
            "INSERT INTO users(id,username,password_hash,role,created_at) VALUES(?,?,?,'admin',?)",
            (
                str(uuid4()),
                settings.admin_username,
                await asyncio.to_thread(hash_password, settings.admin_password),
                now(),
            ),
        )
