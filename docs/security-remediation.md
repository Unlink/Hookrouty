# Security audit remediation

Audit baseline: `f10b6f595c0c5ed484f743d6aab4b49623539fae`. Remediation and verification: 12 November 2026.

## Confirmed findings and fixes

| Finding | Resolution | Regression evidence |
| --- | --- | --- |
| High: captured malformed JSON could permanently consume global proxy slots | Snapshot construction is inside unconditional slot cleanup. Capture safely omits malformed Unicode and excessively nested JSON instead of leaking capacity. | Injected snapshot exception releases capacity; malformed Unicode/deep passthrough bodies preserve subsequent successful calls; packaged Docker roundtrip includes malformed capture inputs. |
| High: management bodies were unbounded before authentication/model validation | ASGI middleware caps declared and actual received bytes before FastAPI parsing. MAX_MANAGEMENT_BODY_BYTES defaults to 1 MiB; management reception has a fixed 15-second total deadline. | Anonymous and authenticated streamed requests stop at the limit; oversized declared length is rejected before consumption; misleading/very long length values, exact boundary, deep JSON and receive timeout have controlled responses. |
| Medium: secret discovery/encoding gaps retained echoed credentials | Discovery traverses arrays conservatively; known-secret collection includes nested and numeric values. Plaintext, JSON-escaped, percent-encoded and form-style encoded values are masked before capture truncation. | Nested-array preview and stored response/header echoes, numeric credentials, plaintext/percent/form representations and capture truncation are covered. |
| Medium: composed dot segments escaped the fixed destination prefix | Completed rendered path segments equal to dot/parent-dot are encoded before HTTPX normalization, including segments composed from fixed text and empty/adjacent substitutions. | Tests assert confinement against the final HTTPX URL, alongside the existing direct-substitution tests. |

The actual target request/response payloads retain their configured types and bytes. Changes to snapshots affect monitoring and previews, not the caller's forwarded payload.

## Additional hardening delivered

- Public hook responses enforce a script-free CSP sandbox with an opaque origin, disallowing forms, embedded resources and framing. Upstream CSP and browser-reporting headers are removed before the policy is applied. Status, body bytes and ordinary transfer headers remain intact. Chromium verifies blocked script execution, opaque origin, denied local storage and continued authenticated management access.
- Anonymous session issuance, expired-row cleanup and the global 1000-unexpired-anonymous-row budget are atomic. Existing sessions are reused, including when issuance capacity is full. New anonymous sessions receive 429 with Retry-After when the budget is exhausted.
- The HTTPX transport now uses the same connection-pool size as the configured admission capacity, resolving the earlier over-100 concurrency footnote.
- All remote workflow actions are pinned to verified commit SHAs. Docker's frontend, uv stage and Python base image are pinned to verified multi-platform manifest digests. Hatchling 1.33.0 and all build-system dependencies are version-pinned. `uv lock` and `uv sync --locked` succeeded; runtime dependency versions in uv.lock did not need to change.

Build pins were verified from their upstream Git refs, Docker manifest indexes and PyPI metadata on 12 November 2026. Updates must deliberately refresh the pins and rerun verification; pinning is not a claim that all dependencies are free of advisories.

## Verification

- Regression-first focused suite: the original 17 cases produced 13 expected failures before the main fixes; all passed afterward. Four further boundary tests exposed three additional issues, which were fixed. Final focused result: **21 passed**.
- Full suite with Chromium and amd64 Docker enabled: **79 passed in 24.25 seconds**, no skipped tests or warnings.
- Separate arm64 Docker smoke under QEMU: **1 passed in 37.29 seconds**.
- Ruff lint: passed. Ruff formatting: passed, 40 Python files.
- Production Compose configuration: passed, including the new management body setting.
- Pinned multi-platform build: amd64 and arm64 succeeded and were loaded locally as `hookrouty:local`. Verified local manifest: `sha256:b64ad1585429e1154dcabb4dea4d025f7593a9faf2f9c43cfddbf1339c2fb10a`.
- Docker smoke verifies runtime API rejection and safe malformed capture, service roundtrip, raw gzip, restart persistence, backup/restore into a fresh volume, password recovery, non-root/read-only runtime and bootstrap behavior.
- Astra performed one independent read-only static remediation review and reported no material security or correctness blocker. It did not rerun tests or remotely verify pins; those checks were performed by the implementer.

## Remaining scope and operating assumptions

Internal HTTP destinations remain an intentional trusted-team capability. The completed-path fix addresses HTTPX normalization; downstream services can have their own decoding/normalization rules and still require their own authorization.

The anonymous-session budget bounds storage. Per-source issuance throttling or reserved login-bootstrap capacity remains optional hardening: anonymous traffic can temporarily fill the global issuance budget. Existing sessions remain usable and expired rows are reclaimed. Deployments exposing administration publicly should apply ingress connection/rate limits as appropriate.

Dedicated webhook/management hostnames can provide additional isolation when reverse-proxy routing prevents administration and management cookies from being exposed on the webhook hostname. Enforced sandbox headers protect browser-rendered hook content in the default single-origin deployment.

This remediation did not include a dependency-advisory scan or a production penetration test. It does not publish a release image. The local commit uses the same per-command unsigned fallback as the initial implementation because interactive GPG signing is unavailable; global Git settings are unchanged.
