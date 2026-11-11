import asyncio
import sqlite3
from uuid import uuid4

from .models import RouterError
from .security import hash_password, now


def require_admin(actor):
    if actor.role != "admin":
        raise RouterError(403, "FORBIDDEN", "Administrator access required.")


async def require_hook_access(db, actor, hook_id, deleted=False):
    sql = "SELECT * FROM hooks WHERE id=?" + ("" if deleted else " AND deleted_at IS NULL")
    args = [hook_id]
    if actor.role != "admin":
        sql += " AND EXISTS(SELECT 1 FROM hook_members WHERE hook_id=hooks.id AND user_id=?)"
        args.append(actor.id)
    hook = await db.one(sql, args)
    if not hook:
        raise RouterError(404, "HOOK_NOT_FOUND", "Hook not found.")
    return hook


async def create_user(db, actor, spec):
    require_admin(actor)
    uid = str(uuid4())
    try:
        await db.execute(
            "INSERT INTO users(id,username,password_hash,role,active,created_at) VALUES(?,?,?,?,?,?)",
            (
                uid,
                spec.username,
                await asyncio.to_thread(hash_password, spec.password),
                spec.role,
                spec.active,
                now(),
            ),
        )
    except sqlite3.IntegrityError:
        raise RouterError(409, "USERNAME_CONFLICT", "Username is already in use.") from None
    return await db.one("SELECT id,username,role,active FROM users WHERE id=?", (uid,))


async def update_user(db, actor, uid, patch):
    require_admin(actor)
    async with db.transaction() as conn:
        async with conn.execute("SELECT * FROM users WHERE id=?", (uid,)) as cursor:
            row = await cursor.fetchone()
        if not row:
            raise RouterError(404, "USER_NOT_FOUND", "User not found.")
        role = patch.role if patch.role is not None else row["role"]
        active = patch.active if patch.active is not None else row["active"]
        if row["role"] == "admin" and row["active"] and (role != "admin" or not active):
            async with conn.execute(
                "SELECT COUNT(*) FROM users WHERE role='admin' AND active=1"
            ) as cursor:
                if (await cursor.fetchone())[0] <= 1:
                    raise RouterError(
                        409,
                        "LAST_ADMIN",
                        "The last active administrator cannot be disabled or demoted.",
                    )
        await conn.execute(
            "UPDATE users SET role=?,active=?,auth_version=auth_version+1 WHERE id=?",
            (role, active, uid),
        )
        await conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    return await db.one("SELECT id,username,role,active FROM users WHERE id=?", (uid,))


async def change_password(db, uid, password):
    if not isinstance(password, str) or not 8 <= len(password) <= 1024:
        raise RouterError(400, "INVALID_INPUT", "Password must contain 8 to 1024 characters.")
    digest = await asyncio.to_thread(hash_password, password)
    async with db.transaction() as conn:
        await conn.execute(
            "UPDATE users SET password_hash=?,auth_version=auth_version+1 WHERE id=?", (digest, uid)
        )
        await conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
