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

The repository is [Unlink/Hookrouty](https://github.com/Unlink/Hookrouty) and the registry image is `ghcr.io/unlink/hookrouty`. The workflow derives this lowercase name from github.repository, so forks automatically use their own namespace. The MIT license from the repository's original main history is preserved and included in the installed distribution.

`.github/workflows/ci.yml` verifies feature-branch pushes and pull requests and is also reusable through workflow_call. It checks locked dependencies, Ruff, unit/integration tests, Chromium, Compose and native Docker smoke tests on Ubuntu amd64 and arm64. It excludes main pushes because the publishing workflow invokes the same checks there.

`.github/workflows/publish.yml` runs on pushes to main, published GitHub releases and manual workflow_dispatch. Verification always runs before the publishing job. A manual run publishes only when main is selected; a manual feature-branch run cannot push an image. Publication uses packages: write only in the publish job; verification and pull requests keep read-only repository access. Jobs are serialized per Git ref to limit overlapping publication.

| Trigger | Tags |
| --- | --- |
| Push to main or manual run on main | latest and sha-<full-commit-sha> |
| Stable release v0.1.0 | 0.1.0, latest and sha-<full-commit-sha> |
| Prerelease v0.2.0-rc.1 | 0.2.0-rc.1 and sha-<full-commit-sha>; latest unchanged |

The GitHub prerelease flag also prevents updating latest. Release names must use vMAJOR.MINOR.PATCH with an optional prerelease suffix; invalid tags fail before login/publication. A GitHub release is an explicit operator action; ordinary tag pushes alone do not publish a release image.

The workflow uses the existing SHA-pinned Docker actions, verifies both architectures before publication, and builds an amd64/arm64 manifest with provenance and an SBOM. The image name, digest and tags appear in the workflow summary. Source, revision and MIT license OCI labels connect the package to the repository.

Authentication is the built-in GITHUB_TOKEN; no custom personal token or Docker Hub credentials are needed. Repository/organization settings must permit packages: write. A first GHCR package may default private: change package visibility for anonymous pulls, or sign in with a token allowed to read it. Registry visibility and Actions permission restrictions are controlled by the repository/package owner.

Use a version or digest for production upgrades. The latest tag follows successful main publications and stable releases, so it is intentionally mutable. The full commit tag is convenient for identifying the source revision; a digest is the immutable deployment reference.

References: [GitHub publishing documentation](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images), [reusable workflows](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows), [Docker build-push-action](https://github.com/docker/build-push-action).

## Changes and review

Keep business rules in services, not just button visibility. Any change to routing, templating, secret handling, account authorization or response byte semantics needs focused regression coverage. Never log request URLs with query strings, bodies, tokens or raw HTTPX exception text. Verify the packaged image, not only an editable source install, after changing assets/migrations.

Future schema migrations belong in `src/hookrouty/migrations` with monotonically numbered filenames. Add a test opening an actual previous-version database and a rollback/recovery note. Do not edit a migration already released. Reopening schema version 1 is idempotent; this initial release has no invented previous migration version.
