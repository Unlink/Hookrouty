"""Local recovery and consistent SQLite backups. Passwords are read from stdin."""

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

from .config import Settings
from .db import Database
from .security import SecretBox
from .users import change_password


async def reset(settings, username, password):
    db = await Database.open(settings.data_dir / "hookrouty.sqlite3")
    try:
        fingerprint = await db.one("SELECT value FROM app_meta WHERE key='key_check'")
        if (
            not fingerprint
            or fingerprint["value"] != SecretBox(settings.app_secret_key).fingerprint
        ):
            raise RuntimeError("APP_SECRET_KEY does not match this database")
        user = await db.one("SELECT id FROM users WHERE username=?", (username,))
        if not user:
            raise RuntimeError("User not found")
        await change_password(db, user["id"], password)
    finally:
        await db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    recovery = commands.add_parser("reset-password")
    recovery.add_argument("--username", required=True)
    recovery.add_argument("--password-stdin", action="store_true", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings()
    try:
        if args.command == "reset-password":
            asyncio.run(reset(settings, args.username, sys.stdin.readline().rstrip("\r\n")))
            print("Password reset; existing sessions invalidated.")
        else:
            if args.output.exists():
                raise RuntimeError("Backup output already exists")
            source = sqlite3.connect(
                f"file:{(settings.data_dir / 'hookrouty.sqlite3').as_posix()}?mode=ro", uri=True
            )
            try:
                with sqlite3.connect(args.output) as destination:
                    source.backup(destination)
            finally:
                source.close()
            print("Database backup created. Store APP_SECRET_KEY separately with it.")
    except Exception:
        print(
            "Operation failed. Check account, key, database and output permissions.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
