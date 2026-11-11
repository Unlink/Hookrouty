# Operations

## Deployment and environment

Run one Uvicorn worker and one application container per SQLite database. Use a local Docker named volume for `/data`. The application is intended for a trusted team: anyone allowed to edit a hook can direct HTTP requests to reachable internal services. Place the service behind HTTPS and restrict network egress where appropriate for your deployment.

| Variable | Default | Meaning |
| --- | --- | --- |
| APP_SECRET_KEY | required | URL-safe Base64 encoding of exactly 32 random bytes |
| ADMIN_USERNAME | admin | Bootstrap username; used only with no existing users |
| ADMIN_PASSWORD | required on first initialization | Initial password, at least 8 characters; ignored after bootstrap |
| APP_URL | http://localhost:8080 | Public base URL; HTTPS enables Secure cookies |
| DATA_DIR | /data | SQLite storage directory |
| LOG_LEVEL | INFO | Uvicorn log level; URL access logs are disabled |
| MAX_INFLIGHT_REQUESTS | 20 | Global destination capacity, no wait queue |
| MAX_REQUEST_BODY_BYTES | 1048576 | Incoming webhook body limit |
| MAX_RENDERED_BODY_BYTES | 1048576 | Assembled destination body limit |
| MAX_UPSTREAM_RESPONSE_BYTES | 8388608 | Destination transfer-byte limit, including encoded responses |
| HISTORY_CAPTURE_BYTES | 16384 | Maximum stored text preview per body after redaction |
| HISTORY_RETENTION_DAYS | 7 | Maximum execution age |
| HISTORY_MAX_RECORDS | 10000 | Maximum completed execution records |
| SHUTDOWN_GRACE_SECONDS | 30 | Wait for active calls during shutdown |

All limits are positive integers. Timeout is per hook, 1–900 seconds, default 60. History cleanup runs every five minutes. Running calls are excluded from retention deletion. Unknown-route counters are daily UTC buckets retained for seven days. Session duration (24h), anonymous duration (15m), login limit (five failures / 15m) and cleanup interval are fixed in v0.1.

Compose-only settings: `HOOKROUTY_IMAGE` selects a version/digest, `HOOKROUTY_BIND_ADDRESS` defaults to 127.0.0.1, `STOP_GRACE_PERIOD` defaults to 35s. They are not application environment settings.

## HTTPS reverse proxy

Set `APP_URL=https://hooks.example.com`. Configure a reverse proxy to terminate TLS and forward the original method, raw path and query to port 8080. Preserve encoded paths; do not decode encoded separators or normalize trailing slashes. Set the proxy body limit at least as large as your webhook limit and its response/idle timeouts above the hook timeout. Keep the service port private when the proxy is the public entry point.

The built-in process does not trust X-Forwarded-For: login rate limiting uses the connection's peer IP. Behind a proxy, users share the proxy-IP login bucket in addition to independent username buckets. This conservative default prevents clients forging their IP. Do not enable multiple workers: sessions persist in SQLite, but in-flight capacity and login throttling are process-local.

For hooks that can take 900 seconds, set `SHUTDOWN_GRACE_SECONDS=905` and `STOP_GRACE_PERIOD=910s`, and adjust the reverse proxy timeout accordingly. Shutdown waits for active tasks within the configured grace period and cancels remaining local waits. A canceled request cannot undo a remote action. Startup changes leftover running executions to interrupted and never resends them.

## Health and logs

`GET /healthz` checks the local database. It returns `{"status":"ok"}` or 503 `{"status":"unavailable"}` without paths or SQL details. Destination outages do not make the app unhealthy. Inspect `docker compose ps` and `docker compose logs --tail=100 hookrouty`. Access logs with complete request URLs are disabled. Application execution logs use request ID, outcome and duration and exclude query values, headers, bodies and raw HTTP exceptions.

Capacity exhaustion returns 503 immediately. Request/render limits return 413; destination response overrun returns 502. These limits are independent of body capture. Encoded upstream bytes are forwarded with their Content-Encoding and a freshly computed Content-Length; compressed bodies are omitted from history.

## Back up

SQLite uses WAL, so do not copy only the live main database file. Use the included online backup command, which uses SQLite's consistent backup API:

```bash
docker compose exec -T hookrouty python -m hookrouty.admin backup --output /data/backup-2026-11-11.sqlite3
docker compose cp hookrouty:/data/backup-2026-11-11.sqlite3 ./backup-2026-11-11.sqlite3
```

Choose a new output filename each time; existing outputs are refused. Store `APP_SECRET_KEY` securely with the database backup, separate from public source control. Backups include users, password hashes, session digests, token hashes, encrypted configuration and history; treat them as sensitive. Retain them according to your own backup policy and remove old backup files explicitly; execution retention does not delete backup files.

## Restore

1. Stop the service: `docker compose stop hookrouty`.
2. Use a new empty named volume; keep the original volume until verification succeeds. With the Compose project name `hookrouty`, the default volume is `hookrouty_hookrouty-data`.
3. Copy the backup into the new volume as `/data/hookrouty.sqlite3`, owned by UID/GID 10001. Restore only into an empty directory; old `-wal` and `-shm` files must not accompany an unrelated backup.
4. Set the original `APP_SECRET_KEY`, point Compose at the new volume, and start. Existing users mean bootstrap credentials are unnecessary.
5. Check health, sign in, verify hook configuration/history, perform a safe preview, and only then perform a controlled real call.

Example for an explicitly chosen empty recovery volume (run from the backup directory):

```bash
docker volume create hookrouty-recovered
docker run --rm --user 0:0 --entrypoint python \
  -v hookrouty-recovered:/data -v "$PWD:/backup:ro" "$HOOKROUTY_IMAGE" \
  -c "import os,shutil; p='/data/hookrouty.sqlite3'; assert not os.listdir('/data'); shutil.copyfile('/backup/backup-2026-11-11.sqlite3',p); os.chown(p,10001,10001); os.chown('/data',10001,10001)"
```

Use a Compose override that sets `volumes: {hookrouty-data: {external: true, name: hookrouty-recovered}}`, or edit the deployment volume definition. The recovery helper deliberately uses root only to set volume ownership; the normal runtime remains non-root. Session cookies from the backed-up period may remain valid until their stored expiry; invalidate passwords/sessions as appropriate for the recovery scenario.

## Reset a forgotten password

This is an explicit local operator action. Passwords are read from stdin, never process arguments:

```bash
read -rs -p 'New admin password: ' NEW_PASSWORD; printf '\n'
printf '%s\n' "$NEW_PASSWORD" | docker compose exec -T hookrouty \
  python -m hookrouty.admin reset-password --username admin --password-stdin
unset NEW_PASSWORD
```

PowerShell:

```powershell
$newPassword = Read-Host 'New admin password'
$newPassword | docker compose exec -T hookrouty python -m hookrouty.admin reset-password --username admin --password-stdin
Remove-Variable newPassword
```

The command checks the database key, updates the account password and invalidates all sessions for that user. It does not change role, active state or other accounts. Resetting bootstrap environment values never modifies existing accounts.

## Upgrade and rollback

1. Back up the database and retain the matching key and current image digest.
2. Review release notes and update `HOOKROUTY_IMAGE` to a version/digest.
3. `docker compose pull && docker compose up -d`.
4. Check health, login, a preview and a controlled roundtrip.

Migrations run before request handling and are recorded in schema_migrations. Startup refuses a mismatched key. v0.1 is the first schema; upgrades currently exercise idempotent reopening rather than a fictional older release. Future schema migrations must include previous-version fixtures. Downgrading an image after a schema change may be incompatible: restore the matching backup into a separate volume for a reliable rollback. Do not rotate APP_SECRET_KEY as an ordinary environment edit; v0.1 has no online secret re-encryption workflow.
## Advanced concurrency note

The default 20 concurrent destination calls is fully bounded and tested. If raising MAX_INFLIGHT_REQUESTS above 100, HTTPX's transport currently retains its default connection pool and may wait internally within the total network deadline. Tune the transport pool together with that advanced deployment change; see the deferred minor in [implementation status](implementation-status.md).
