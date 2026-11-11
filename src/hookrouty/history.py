import json
from datetime import UTC, datetime, timedelta

from .models import RouterError
from .security import now
from .users import require_hook_access


class History:
    def __init__(self, state):
        self.state = state

    async def start(self, rid, hook, snapshot, source, attempted=True, outcome="running"):
        await self.state.db.execute(
            "INSERT INTO executions(request_id,hook_id,hook_name_snapshot,source,attempted_target,started_at,finished_at,outcome,safe_snapshot) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                rid,
                hook["id"],
                hook["name"],
                source,
                attempted,
                now(),
                None if outcome == "running" else now(),
                outcome,
                json.dumps(snapshot),
            ),
        )

    async def finish(self, rid, outcome, status, duration, snapshot):
        await self.state.db.execute(
            "UPDATE executions SET finished_at=?,outcome=?,upstream_status=?,duration_ms=?,safe_snapshot=? WHERE request_id=? AND outcome='running'",
            (now(), outcome, status, duration, json.dumps(snapshot), rid),
        )

    def filters(self, actor, query):
        clauses, args = ["1=1"], []
        if actor.role != "admin":
            clauses.append(
                "EXISTS(SELECT 1 FROM hook_members m WHERE m.hook_id=e.hook_id AND m.user_id=?)"
            )
            args.append(actor.id)
        for field, column in [("hook_id", "hook_id"), ("outcome", "outcome"), ("source", "source")]:
            if query.get(field):
                clauses.append(f"e.{column}=?")
                args.append(query[field])
        for key, operator in [("since", ">="), ("until", "<=")]:
            if query.get(key):
                try:
                    stamp = datetime.fromisoformat(query[key].replace("Z", "+00:00"))
                    if stamp.tzinfo is None:
                        raise ValueError()
                    stamp = (
                        stamp.astimezone(UTC)
                        .isoformat(timespec="microseconds")
                        .replace("+00:00", "Z")
                    )
                except ValueError:
                    raise RouterError(
                        400, "INVALID_INPUT", "Use an ISO 8601 timestamp with timezone."
                    ) from None
                clauses.append(f"e.started_at{operator}?")
                args.append(stamp)
        return " AND ".join(clauses), args

    async def list(self, actor, query):
        where, args = self.filters(actor, query)
        try:
            page = max(1, int(query.get("page", 1)))
            size = min(100, max(1, int(query.get("page_size", 25))))
        except ValueError:
            raise RouterError(400, "INVALID_INPUT", "Invalid pagination.") from None
        rows = await self.state.db.all(
            f"SELECT * FROM executions e WHERE {where} ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (*args, size, (page - 1) * size),
        )
        total = await self.state.db.one(
            f"SELECT COUNT(*) AS n FROM executions e WHERE {where}", args
        )
        for row in rows:
            row["safe_snapshot"] = json.loads(row["safe_snapshot"])
        return {"items": rows, "total": total["n"], "page": page, "page_size": size}

    async def detail(self, actor, rid):
        row = await self.state.db.one("SELECT * FROM executions WHERE request_id=?", (rid,))
        if not row:
            raise RouterError(404, "EXECUTION_NOT_FOUND", "Execution not found.")
        await require_hook_access(self.state.db, actor, row["hook_id"], deleted=True)
        row["safe_snapshot"] = json.loads(row["safe_snapshot"])
        row["uncertain_result"] = row["outcome"] in {
            "timeout",
            "client_disconnected",
            "interrupted",
            "proxy_error",
        }
        return row

    async def stats(self, actor, query):
        period = query.get("period", "24h")
        if period not in {"24h", "7d"}:
            raise RouterError(400, "INVALID_INPUT", "Period must be 24h or 7d.")
        filters = dict(query)
        filters["since"] = (
            datetime.now(UTC) - timedelta(days=7 if period == "7d" else 1)
        ).isoformat()
        where, args = self.filters(actor, filters)
        rows = await self.state.db.all(
            f"SELECT outcome,attempted_target,duration_ms FROM executions e WHERE {where}", args
        )
        completed = [
            r
            for r in rows
            if r["attempted_target"]
            and r["outcome"] not in {"running", "interrupted", "client_disconnected"}
        ]
        success = sum(r["outcome"] == "success" for r in completed)
        result = {
            "period": period,
            "calls": len(rows),
            "completed_attempts": len(completed),
            "successes": success,
            "success_rate": success / len(completed) if completed else None,
            "average_duration_ms": sum(r["duration_ms"] or 0 for r in completed) / len(completed)
            if completed
            else None,
            "running": sum(r["outcome"] == "running" for r in rows),
            "rejected": sum(not r["attempted_target"] for r in rows),
            "outcomes": {
                name: sum(r["outcome"] == name for r in rows)
                for name in {r["outcome"] for r in rows}
            },
        }
        if actor.role == "admin":
            counter = await self.state.db.one(
                "SELECT COALESCE(SUM(count),0) AS n FROM aggregate_counters WHERE bucket_start>=?",
                (filters["since"][:10],),
            )
            result["unknown_routes"] = counter["n"]
        return result

    async def prune(self):
        settings = self.state.settings
        cutoff = (
            (datetime.now(UTC) - timedelta(days=settings.history_retention_days))
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
        async with self.state.db.transaction() as conn:
            await conn.execute("DELETE FROM sessions WHERE expires_at<?", (now(),))
            await conn.execute(
                "DELETE FROM executions WHERE outcome!='running' AND (started_at<? OR request_id IN (SELECT request_id FROM executions WHERE outcome!='running' ORDER BY started_at DESC LIMIT -1 OFFSET ?))",
                (cutoff, settings.history_max_records),
            )
            await conn.execute(
                "DELETE FROM aggregate_counters WHERE bucket_start<?",
                ((datetime.now(UTC) - timedelta(days=7)).date().isoformat(),),
            )
            active = list(self.state.proxy.active)
            placeholders = ",".join("?" for _ in active) or "''"
            await conn.execute(
                f"UPDATE executions SET outcome='interrupted',finished_at=? WHERE outcome='running' AND request_id NOT IN ({placeholders})",
                (now(), *active),
            )
