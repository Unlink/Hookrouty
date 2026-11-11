import copy
import json
from uuid import uuid4

from .models import HookSpec, RouterError
from .routing import RouteConflict, compile_route, validate_route_set
from .security import new_token, now, token_digest
from .users import require_admin, require_hook_access


def encode_config(spec, box):
    config = spec.model_dump()
    config["auth"].pop("token", None)
    for item in config["target"]["query"] + config["target"]["headers"]:
        item["id"] = item["id"] or str(uuid4())
        if item["secret"]:
            item["value"] = box.encrypt(item["value"] or "")
    body = config["target"]["body"]
    if body["secret"]:
        body["template"] = box.encrypt(json.dumps(body["template"]))
    return json.dumps(config)


def decode_config(row, box):
    config = json.loads(row["config_json"])
    for item in config["target"]["query"] + config["target"]["headers"]:
        if item["secret"]:
            item["value"] = box.decrypt(item["value"])
    body = config["target"]["body"]
    if body["secret"]:
        body["template"] = json.loads(box.decrypt(body["template"]))
    return HookSpec.model_validate(config)


def safe_hook(row, box):
    config = decode_config(row, box).model_dump()
    config["auth"].pop("token", None)
    for item in config["target"]["query"] + config["target"]["headers"]:
        if item["secret"]:
            item.update(value=None, configured=True, value_action="keep")
    body = config["target"]["body"]
    if body["secret"]:
        body.update(template=None, configured=True, value_action="keep")
    return {"id": row["id"], **config}


async def save_hook(state, actor, spec, hook_id=None):
    existing = await require_hook_access(state.db, actor, hook_id) if hook_id else None
    if existing:
        old = decode_config(existing, state.box)
        for kind in ("headers", "query"):
            old_fields = {f.id: f for f in getattr(old.target, kind)}
            kept = []
            for item in getattr(spec.target, kind):
                if item.value_action == "remove":
                    continue
                if item.value_action == "keep":
                    previous = old_fields.get(item.id)
                    if previous is None or not previous.secret:
                        raise RouterError(400, "INVALID_INPUT", "Unknown secret field.")
                    if item.name != previous.name or not item.secret:
                        raise RouterError(
                            400,
                            "INVALID_INPUT",
                            "Kept secrets must preserve their name and secret flag.",
                        )
                    item.value = previous.value
                elif item.value is None:
                    raise RouterError(400, "INVALID_INPUT", "Replacement fields require a value.")
                item.value_action = "replace"
                kept.append(item)
            setattr(spec.target, kind, kept)
        if spec.target.body.value_action == "keep":
            spec.target.body = copy.deepcopy(old.target.body)
        elif spec.target.body.value_action == "remove":
            from .models import BodySpec

            spec.target.body = BodySpec()
        spec = HookSpec.model_validate(spec.model_dump())
    else:
        if (
            any(
                item.value_action != "replace" or item.value is None
                for item in spec.target.query + spec.target.headers
            )
            or spec.target.body.value_action != "replace"
        ):
            raise RouterError(400, "INVALID_INPUT", "New fields require values.")
    token = spec.auth.token or (None if existing else new_token())
    spec.auth.token = None
    hid = hook_id or str(uuid4())
    async with state.db.transaction() as conn:
        async with conn.execute(
            "SELECT id,input_method,input_path FROM hooks WHERE deleted_at IS NULL AND id!=?",
            (hid,),
        ) as cursor:
            rows = await cursor.fetchall()
        routes = [compile_route(r["input_method"], r["input_path"], r["id"]) for r in rows]
        try:
            validate_route_set(routes + [compile_route(spec.input_method, spec.input_path, hid)])
        except RouteConflict:
            raise RouterError(
                409, "ROUTE_CONFLICT", "A route with the same priority already exists."
            ) from None
        config = encode_config(spec, state.box)
        if existing:
            await conn.execute(
                "UPDATE hooks SET name=?,enabled=?,input_method=?,input_path=?,config_json=? WHERE id=? AND deleted_at IS NULL",
                (spec.name, spec.enabled, spec.input_method, spec.input_path, config, hid),
            )
            if token:
                await conn.execute(
                    "UPDATE hooks SET token_hash=? WHERE id=?", (token_digest(token), hid)
                )
        else:
            await conn.execute(
                "INSERT INTO hooks VALUES(?,?,?,?,?,?,?,?,NULL)",
                (
                    hid,
                    spec.name,
                    spec.enabled,
                    spec.input_method,
                    spec.input_path,
                    token_digest(token),
                    config,
                    actor.id,
                ),
            )
            await conn.execute("INSERT INTO hook_members VALUES(?,?)", (hid, actor.id))
    row = await state.db.one("SELECT * FROM hooks WHERE id=?", (hid,))
    return {"id": hid, **({"token": token} if token else {}), "hook": safe_hook(row, state.box)}


async def set_hook_members(state, actor, hid, ids):
    require_admin(actor)
    await require_hook_access(state.db, actor, hid, deleted=True)
    async with state.db.transaction() as conn:
        for uid in ids:
            async with conn.execute("SELECT id FROM users WHERE id=?", (uid,)) as cursor:
                if not await cursor.fetchone():
                    raise RouterError(400, "INVALID_INPUT", "Unknown member.")
        await conn.execute("DELETE FROM hook_members WHERE hook_id=?", (hid,))
        await conn.executemany(
            "INSERT INTO hook_members VALUES(?,?)", [(hid, uid) for uid in set(ids)]
        )


async def delete_hook(state, actor, hid):
    await require_hook_access(state.db, actor, hid)
    await state.db.execute(
        "UPDATE hooks SET enabled=0,token_hash=NULL,config_json=NULL,deleted_at=? WHERE id=?",
        (now(), hid),
    )
