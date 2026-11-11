import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite


class Database:
    def __init__(self, connection):
        self.connection = connection
        self.lock = asyncio.Lock()

    @classmethod
    async def open(cls, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(path, isolation_level=None)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA journal_mode=WAL")
        await connection.execute("PRAGMA foreign_keys=ON")
        await connection.execute("PRAGMA busy_timeout=5000")
        return cls(connection)

    async def migrate(self):
        await self.connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY)"
        )
        for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
            version = int(migration.stem.split("_")[0])
            if not await self.one(
                "SELECT version FROM schema_migrations WHERE version=?", (version,)
            ):
                sql = migration.read_text(encoding="utf-8")
                await self.connection.executescript(
                    f"BEGIN IMMEDIATE;\n{sql}\nINSERT INTO schema_migrations VALUES({version});\nCOMMIT;"
                )

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            await self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
                await self.connection.commit()
            except BaseException:
                await self.connection.rollback()
                raise

    async def one(self, sql, args=()):
        async with self.lock, self.connection.execute(sql, args) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def all(self, sql, args=()):
        async with self.lock, self.connection.execute(sql, args) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def execute(self, sql, args=()):
        async with self.lock:
            return await self.connection.execute(sql, args)

    async def close(self):
        await self.connection.close()
