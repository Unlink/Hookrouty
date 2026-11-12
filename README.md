# Hookrouty

A small, synchronous webhook proxy for a trusted team. Configure incoming routes and one HTTP destination per hook in the browser, then follow requests in a private activity history.

- Parameterized routes under `/hooks`, with independent webhook tokens.
- Destination templates using path, query, header and JSON body values.
- Administrator and member accounts, per-hook assignments, server-side sessions and CSRF protection.
- Safe request previews, real manual tests, monitoring and retained execution history.
- SQLite persistence, one non-root Docker container, no host-side configuration files required.

## Run with Docker Compose

Requirements: Docker Engine/Desktop and Docker Compose v2. The runtime supports Linux amd64 and arm64.

```bash
docker build -t hookrouty:local .
export HOOKROUTY_IMAGE=hookrouty:local
export APP_SECRET_KEY="$(python3 -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')"
export ADMIN_PASSWORD='choose-a-long-unique-password'
export APP_URL=http://localhost:8080
docker compose up -d
```

PowerShell:

```powershell
docker build -t hookrouty:local .
$env:HOOKROUTY_IMAGE = 'hookrouty:local'
$env:APP_SECRET_KEY = python -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'
$env:ADMIN_PASSWORD = 'choose-a-long-unique-password'
$env:APP_URL = 'http://localhost:8080'
docker compose up -d
```

Open [Hookrouty](http://localhost:8080). Sign in as `admin` with the bootstrap password. Keep the generated key in your deployment secret store: losing it makes encrypted destination fields unusable. The key is also required on every restart. Remove `ADMIN_PASSWORD` from the deployment after successful initialization; it is ignored when accounts already exist.

The supplied [compose.yaml](compose.yaml) uses a named volume, read-only root filesystem, dropped capabilities and a healthcheck. It binds port 8080 to loopback by default. Set `HOOKROUTY_BIND_ADDRESS=0.0.0.0` for remote access, or connect a reverse proxy to the service. An optional [.env.example](.env.example) documents the deployment inputs; environment variables work without any `.env` file.

## First webhook

1. In **Users**, an administrator can create accounts. In **Hooks**, any signed-in user can create a hook; its creator receives access automatically.
2. Choose input method `POST`, route `/hooks/deploy/:project/:environment`, and enable the public endpoint.
3. Set destination method `POST` and URL `http://deployment-service:8000/apps/{{path.project}}/deploy`.
4. Add query field `environment` with value `{{path.environment}}`. Add any destination credentials as secret headers.
5. Select JSON body mode and enter `{"version":"{{query.version}}","force":"{{body.force}}"}`.
6. Save and securely copy the token shown once. Use **Test** to inspect a masked preview. **Send real test** contacts the destination once and records `source=manual_test`, including for disabled hooks.

```bash
curl --fail-with-body \
  -X POST 'http://localhost:8080/hooks/deploy/my-api/production?version=1.2.3' \
  -H 'X-Webhook-Token: REPLACE_WITH_GENERATED_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"force":true}'
```

The caller waits for the destination response. HTTP 4xx/5xx responses are forwarded too; redirects are returned without following them. There is no automatic retry. Timeout or disconnection can leave the remote action completed even when no response was obtained. Use the request ID to investigate before deciding whether to repeat a call.

**Activity** shows calls, success rate, average duration, running requests, safe details and filters. Success means a completed destination attempt with HTTP 2xx. Local rejections do not affect the success-rate denominator. Polling runs every five seconds while the tab is visible. Assign members from a hook's editor; members see only assigned hooks and history. Administrators see all records.

## Documentation

- [User guide](docs/user-guide.md): configuration, templates, secret editing, previews and monitoring.
- [API reference](docs/api.md): sessions, CSRF, schemas, endpoint contracts and errors.
- [Operations](docs/operations.md): environment, HTTPS, backup, restore, recovery and upgrades.
- [Architecture and security](docs/architecture.md): routing, execution lifecycle, storage and trust boundaries.
- [Development and releases](docs/development.md): tests, Docker builds and GitHub Actions publishing.
- [Security remediation](docs/security-remediation.md): audit findings, fixes and regression coverage.
- [Implementation status](docs/implementation-status.md): verification evidence and decisions.
- [Original implementation specification](docs/implementation-plan.md): supplied Slovak source document, preserved for traceability.

## Build and publish

The workflows are ready for a GitHub repository; no image has been published by this local implementation. CI runs lint, unit/integration tests, Chromium and native container smoke tests for amd64 and arm64. Publishing a GitHub release tagged `v0.1.0` triggers verification followed by a multi-platform push to `ghcr.io/<owner>/<repository>:0.1.0`. Stable releases also update `latest`; prereleases do not. Authentication uses `GITHUB_TOKEN` with `packages: write`. See [release setup](docs/development.md#release-and-ghcr).

For deployment after a release, set `HOOKROUTY_IMAGE=ghcr.io/<owner>/<repository>:0.1.0`. Prefer a version or immutable digest. No specific repository namespace is hard-coded.

## Local development

Python 3.13 and uv 0.13.7 are used for the verified build. Exact dependencies are in [uv.lock](uv.lock), including FastAPI 0.162.4, HTTPX 0.28.1, Jinja2 3.1.6, aiosqlite 0.22.1, Pydantic Settings 2.16.2, Argon2 25.1.0, cryptography 49.0.0 and Uvicorn 0.54.0. Production images exclude pytest, Ruff and Playwright.

```bash
uv sync --locked --python 3.13
export DATA_DIR=./data
# Set APP_SECRET_KEY and ADMIN_PASSWORD as above.
uv run python -m hookrouty
uv run ruff check .
uv run ruff format --check .
uv run pytest tests -q -m 'not e2e and not container'
uv run playwright install chromium
RUN_E2E=1 uv run pytest tests/e2e -q
HOOKROUTY_IMAGE=hookrouty:local uv run pytest tests/container -q
```

Use one application worker and local storage for SQLite. This version has no workflow engine, scheduler, retry queue, public registration or email password recovery.
