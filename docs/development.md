# Development and releases

## Toolchain

Use Python 3.13 and uv 0.13.7. Dependency versions and distribution hashes are locked in uv.lock. Do not regenerate locks during a container build. The project uses a src layout, Hatch packaging and packaged Jinja/static/migration assets. GitHub Actions are pinned to full commit SHAs; Docker base images, uv and the Dockerfile frontend are pinned to multi-platform manifest digests. Hatchling and its build dependencies have exact build-system requirements. Pins were verified against upstream refs on 12 November 2026. Update them deliberately: resolve the intended release ref/digest, review upstream changes, refresh uv.lock, then rerun both architecture builds and regression suites.

The non-root runtime image includes only production dependencies; the build stage installs uv and compiles the environment.

```bash
uv sync --locked --python 3.13
uv run ruff check .
uv run ruff format --check .
uv run pytest tests -q -m 'not e2e and not container'
```

Set APP_SECRET_KEY, ADMIN_PASSWORD and a writable DATA_DIR before `uv run python -m hookrouty`. On Windows, use PowerShell environment assignments. For restricted environments that cannot use the system temp directory, pass pytest `--basetemp .tmp-tests` and add that directory to local ignores.

## Test layers

- Unit/integration: real SQLite and ASGI application with HTTPX transports; auth, CSRF, accounts, memberships, routes, templates, redaction, retention, timeout/capacity, disconnect and storage failure injection.
- Browser: Chromium starts a real Uvicorn process and performs admin/member journeys, safe preview, real manual-test failure, history, inert stored XSS and responsive checks at 1440/390 px.
- Container: real non-root runtime with read-only filesystem, temporary named volume/network and a separate upstream container. Tests roundtrip, raw gzip transfer, cookie filtering, restart, persistent users/hooks/history, backup and password recovery; also existing DB without bootstrap password and missing password on a fresh DB.

```bash
uv run playwright install chromium
RUN_E2E=1 uv run pytest tests/e2e -q
docker build -t hookrouty:local .
HOOKROUTY_IMAGE=hookrouty:local uv run pytest tests/container -q
HOOKROUTY_IMAGE=hookrouty:local APP_SECRET_KEY=<valid-key> docker compose config --quiet
```

PowerShell: set `$env:RUN_E2E='1'` or `$env:HOOKROUTY_IMAGE='hookrouty:local'` before the pytest command. Unselected opt-in browser/container tests are skipped by the default suite. Container tests create uniquely named `hookrouty-test-*` resources and clean up their own containers, network and volume. Do not use a production volume as a test fixture.

For an arm64 build on amd64, install QEMU/binfmt in Docker Desktop or use a native runner. This is an operator-approved host-level setup action. A normal native build needs no privileged runtime.

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  --output type=oci,dest=hookrouty-multiarch.tar .
```

## Release and GHCR

The project intentionally does not hard-code an owner or repository because this workspace began without a Git remote. Create/connect your GitHub repository and push the implementation branch using your usual review process. Enable Actions and allow the workflow's GITHUB_TOKEN to write packages. For an organization, ensure package creation is allowed.

`.github/workflows/ci.yml` runs on pushes/pull requests and as a reusable workflow. It verifies locked dependencies, Ruff, unit/integration tests, Chromium, Compose validation and native Docker smoke tests on Ubuntu amd64 and Ubuntu arm64. Pull requests receive read-only repository permissions and never publish images.

`.github/workflows/publish.yml` runs only when you publish a GitHub release. Use a release tag `vMAJOR.MINOR.PATCH`, optionally with `-prerelease`. It first invokes all CI jobs at that release commit. Only after successful verification does it authenticate to GHCR with the built-in GITHUB_TOKEN and publish an amd64/arm64 manifest, provenance and SBOM.

- `v0.1.0` → `ghcr.io/<lowercase-owner>/<lowercase-repository>:0.1.0` and `:latest`.
- `v0.2.0-rc.1` → `:0.2.0-rc.1`; latest is unchanged.
- A GitHub prerelease flag also prevents a latest update.

The login secret is not a custom personal token. GitHub must allow `packages: write` on the publishing job. The first GHCR package may default private; change package visibility if anonymous deployment should work, or sign in with a token allowed to read it. Published packages should be associated with the source repository through the OCI source label. No local Docker build or CI pull-request test pushes a registry image.

These workflows have been inspected locally; their GitHub-hosted execution can only be verified after the repository exists and receives a push/release. The source references used to prepare the workflow are [GitHub publishing documentation](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images), [Docker build-push-action](https://github.com/docker/build-push-action), [Docker login-action](https://github.com/docker/login-action), and [uv Docker integration](https://docs.astral.sh/uv/guides/integration/docker/).

## Changes and review

Keep business rules in services, not just button visibility. Any change to routing, templating, secret handling, account authorization or response byte semantics needs focused regression coverage. Never log request URLs with query strings, bodies, tokens or raw HTTPX exception text. Verify the packaged image, not only an editable source install, after changing assets/migrations.

Future schema migrations belong in `src/hookrouty/migrations` with monotonically numbered filenames. Add a test opening an actual previous-version database and a rollback/recovery note. Do not edit a migration already released. Reopening schema version 1 is idempotent; this initial release has no invented previous migration version.
