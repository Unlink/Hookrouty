from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .auth import current_principal, new_session, require_csrf, session
from .hooks import decode_config, delete_hook, safe_hook, save_hook, set_hook_members
from .models import HookSpec, IncomingRequest, RouterError, UserCreate, UserPatch
from .redaction import known_secrets, rendered_snapshot, scrub_known
from .security import new_token, token_digest, verify_password
from .users import change_password, create_user, require_admin, require_hook_access, update_user

router = APIRouter(prefix="/api")


async def read_object(request):
    data = await request.json()
    if not isinstance(data, dict):
        raise RouterError(400, "INVALID_INPUT", "A JSON object is required.")
    return data


@router.get("/session")
async def get_session(request: Request):
    row = await session(request)
    if row and (
        not row["user_id"] or (row["active"] and row["auth_version"] == row["current_version"])
    ):
        return {
            "csrf_token": row["csrf_token"],
            "user": {"id": row["user_id"], "username": row["username"], "role": row["role"]}
            if row["user_id"]
            else None,
        }
    response = JSONResponse({})
    data = await new_session(request, response)
    response.body = JSONResponse(data).body
    response.headers["content-length"] = str(len(response.body))
    return response


@router.post("/login")
async def login(request: Request):
    await require_csrf(request)
    data = await read_object(request)
    user = await request.app.state.limiter.authenticate(
        request, str(data.get("username", ""))[:64], str(data.get("password", ""))[:1024]
    )
    await request.app.state.db.execute(
        "DELETE FROM sessions WHERE token_digest=?",
        (request.app.state.box.session_digest(request.cookies.get("hookrouty_session", "")),),
    )
    response = JSONResponse({})
    result = await new_session(request, response, user)
    response.body = JSONResponse(result).body
    response.headers["content-length"] = str(len(response.body))
    return response


@router.post("/logout", status_code=204)
async def logout(request: Request):
    await require_csrf(request)
    await request.app.state.db.execute(
        "DELETE FROM sessions WHERE token_digest=?",
        (request.app.state.box.session_digest(request.cookies.get("hookrouty_session", "")),),
    )
    response = Response(status_code=204)
    response.delete_cookie("hookrouty_session")
    return response


@router.post("/me/password", status_code=204)
async def own_password(request: Request):
    actor = await current_principal(request)
    await require_csrf(request)
    data = await read_object(request)
    user = await request.app.state.db.one("SELECT password_hash FROM users WHERE id=?", (actor.id,))
    if not verify_password(data.get("current_password", ""), user["password_hash"]):
        raise RouterError(400, "INVALID_PASSWORD", "Current password is incorrect.")
    await change_password(request.app.state.db, actor.id, data.get("password"))
    return Response(status_code=204)


@router.get("/users")
async def users(request: Request):
    require_admin(await current_principal(request))
    return await request.app.state.db.all(
        "SELECT id,username,role,active FROM users ORDER BY username"
    )


@router.post("/users", status_code=201)
async def add_user(request: Request, spec: UserCreate):
    actor = await current_principal(request)
    await require_csrf(request)
    return await create_user(request.app.state.db, actor, spec)


@router.patch("/users/{uid}")
async def patch_user(uid: str, patch: UserPatch, request: Request):
    actor = await current_principal(request)
    await require_csrf(request)
    return await update_user(request.app.state.db, actor, uid, patch)


@router.post("/users/{uid}/password", status_code=204)
async def reset_user_password(uid: str, request: Request):
    require_admin(await current_principal(request))
    await require_csrf(request)
    if not await request.app.state.db.one("SELECT id FROM users WHERE id=?", (uid,)):
        raise RouterError(404, "USER_NOT_FOUND", "User not found.")
    await change_password(request.app.state.db, uid, (await read_object(request)).get("password"))
    return Response(status_code=204)


@router.get("/hooks")
async def hooks(request: Request):
    actor = await current_principal(request)
    sql, args = "SELECT * FROM hooks WHERE deleted_at IS NULL", []
    if actor.role != "admin":
        sql += " AND EXISTS(SELECT 1 FROM hook_members WHERE hook_id=hooks.id AND user_id=?)"
        args.append(actor.id)
    return [
        safe_hook(row, request.app.state.box)
        for row in await request.app.state.db.all(sql + " ORDER BY name", args)
    ]


@router.post("/hooks", status_code=201)
async def add_hook(request: Request, spec: HookSpec):
    actor = await current_principal(request)
    await require_csrf(request)
    return await save_hook(request.app.state, actor, spec)


@router.get("/hooks/{hid}")
async def get_hook(hid: str, request: Request):
    actor = await current_principal(request)
    row = await require_hook_access(request.app.state.db, actor, hid)
    return safe_hook(row, request.app.state.box)


@router.patch("/hooks/{hid}")
async def edit_hook(hid: str, request: Request):
    actor = await current_principal(request)
    await require_csrf(request)
    row = await require_hook_access(request.app.state.db, actor, hid)
    data = await read_object(request)
    data.pop("id", None)
    original = safe_hook(row, request.app.state.box)
    original.pop("id")

    def merge(old, patch):
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(old.get(key), dict):
                merge(old[key], value)
            else:
                old[key] = value
        return old

    return (
        await save_hook(
            request.app.state, actor, HookSpec.model_validate(merge(original, data)), hid
        )
    )["hook"]


@router.delete("/hooks/{hid}", status_code=204)
async def remove_hook(hid: str, request: Request):
    actor = await current_principal(request)
    await require_csrf(request)
    await delete_hook(request.app.state, actor, hid)
    return Response(status_code=204)


@router.put("/hooks/{hid}/members")
async def members(hid: str, request: Request):
    actor = await current_principal(request)
    await require_csrf(request)
    data = await read_object(request)
    ids = data.get("user_ids", [])
    if not isinstance(ids, list) or not all(isinstance(uid, str) for uid in ids):
        raise RouterError(400, "INVALID_INPUT", "user_ids must be a list of IDs.")
    await set_hook_members(request.app.state, actor, hid, ids)
    return {"user_ids": ids}


@router.get("/hooks/{hid}/members")
async def get_members(hid: str, request: Request):
    actor = await current_principal(request)
    require_admin(actor)
    await require_hook_access(request.app.state.db, actor, hid, deleted=True)
    return {
        "user_ids": [
            r["user_id"]
            for r in await request.app.state.db.all(
                "SELECT user_id FROM hook_members WHERE hook_id=?", (hid,)
            )
        ]
    }


@router.post("/hooks/{hid}/rotate-token")
async def rotate(hid: str, request: Request):
    actor = await current_principal(request)
    await require_csrf(request)
    await require_hook_access(request.app.state.db, actor, hid)
    data = await read_object(request)
    token = data.get("token", new_token())
    if not isinstance(token, str) or not 1 <= len(token) <= 4096:
        raise RouterError(
            400, "INVALID_INPUT", "Token must be nonempty and at most 4096 characters."
        )
    await request.app.state.db.execute(
        "UPDATE hooks SET token_hash=? WHERE id=?", (token_digest(token), hid)
    )
    return {"id": hid, "token": token}


@router.post("/hooks/{hid}/duplicate", status_code=201)
async def duplicate(hid: str, request: Request):
    actor = await current_principal(request)
    await require_csrf(request)
    row = await require_hook_access(request.app.state.db, actor, hid)
    spec = decode_config(row, request.app.state.box)
    data = await read_object(request)
    spec.input_path = data.get("input_path", "")
    spec.name = data.get("name", spec.name + " (copy)")
    spec.enabled = False
    spec.auth.token = None
    return await save_hook(request.app.state, actor, HookSpec.model_validate(spec.model_dump()))


async def sample_request(hid, request):
    actor = await current_principal(request)
    await require_csrf(request)
    hook = await require_hook_access(request.app.state.db, actor, hid)
    data = await read_object(request)
    if not isinstance(data.get("body", ""), str) or not isinstance(data.get("path", {}), dict):
        raise RouterError(
            400, "INVALID_INPUT", "Sample body must be text and path must be an object."
        )
    for name in ("query", "headers"):
        if not isinstance(data.get(name, []), list) or not all(
            isinstance(pair, list) and len(pair) == 2 and all(isinstance(v, str) for v in pair)
            for pair in data.get(name, [])
        ):
            raise RouterError(
                400, "INVALID_INPUT", "Sample query and headers must be lists of string pairs."
            )
    incoming = IncomingRequest(
        request.state.request_id,
        hook["input_method"],
        hook["input_path"].encode(),
        data.get("query", []),
        data.get("headers", []),
        data.get("body", "").encode(),
        data.get("path", {}),
    )
    return hook, incoming


@router.post("/hooks/{hid}/preview")
async def preview(hid: str, request: Request):
    hook, incoming = await sample_request(hid, request)
    spec = decode_config(hook, request.app.state.box)
    rendered, context = request.app.state.proxy.render(spec, incoming)
    return scrub_known(
        rendered_snapshot(
            rendered,
            spec,
            request.app.state.settings.history_capture_bytes,
            preview=True,
            known_values=known_secrets(context, rendered, spec),
        ),
        known_secrets(context, rendered, spec),
    )


@router.post("/hooks/{hid}/test")
async def manual_test(hid: str, request: Request):
    hook, incoming = await sample_request(hid, request)

    async def disconnected():
        while True:
            if (await request.receive())["type"] == "http.disconnect":
                return

    reply = await request.app.state.proxy.execute(
        hook, incoming, disconnected, source="manual_test"
    )
    # Test UI consumes only the safe stored snapshot, never executable upstream HTML.
    actor = await current_principal(request)
    try:
        execution = await request.app.state.history.detail(actor, incoming.request_id)
    except Exception:
        execution = None
    return {
        "request_id": incoming.request_id,
        "status_code": reply.status_code,
        "execution": execution,
        "history_status": reply.headers.get("X-Hookrouty-History-Status", "ok"),
    }


@router.get("/executions")
async def executions(request: Request):
    actor = await current_principal(request)
    if request.query_params.get("hook_id"):
        await require_hook_access(
            request.app.state.db, actor, request.query_params["hook_id"], deleted=True
        )
    return await request.app.state.history.list(actor, request.query_params)


@router.get("/executions/{rid}")
async def execution(rid: str, request: Request):
    return await request.app.state.history.detail(await current_principal(request), rid)


@router.get("/stats")
async def stats(request: Request):
    actor = await current_principal(request)
    if request.query_params.get("hook_id"):
        await require_hook_access(
            request.app.state.db, actor, request.query_params["hook_id"], deleted=True
        )
    return await request.app.state.history.stats(actor, request.query_params)
