# Implementation status

Source specification: [implementation-plan.md](implementation-plan.md). The supplied source content is preserved; trailing whitespace is normalized. All authored UI, code and documentation are English. Verification date: 11 November 2026.

## Delivered scope

| Task | Result | Evidence |
| --- | --- | --- |
| 1. Foundation and database | Delivered | Settings validation, SQLite WAL/foreign keys, packaged versioned migration, persistent health, idempotent reopening |
| 2. Authentication and encryption | Delivered | Argon2id, independent derived session/encryption keys, server sessions/CSRF, rotation/expiry/lockout, bootstrap, wrong-key refusal, recovery CLI |
| 3. Accounts and hook access | Delivered | Admin accounts, member isolation and revocation, session invalidation, concurrent last-admin protection |
| 4. Hook management and routing | Delivered | CRUD, token rotation, disabled duplication, tombstones, assignments, raw-path validation, deterministic priority, method/slash handling |
| 5. Templates and preview | Delivered | Restricted references, JSON type preservation, escaping, fixed authority, dot-segment containment, secret taint and no-network preview |
| 6. History and monitoring | Delivered | Scoped pagination/filtering/stats, UTC timestamps, optional masked body capture, retention and orphan reconciliation |
| 7. HTTP proxy | Delivered | Single attempt, deadline, raw buffering, compression/binary/HEAD/204/304, limits/capacity, disconnects, storage failure semantics |
| 8. Web interface | Delivered | Responsive English management UI, fields/secret actions, assignments, real tests, activity polling, users/profile, inert XSS |
| 9. Packaging and documentation | Delivered locally | Non-root image, hardened Compose, both architectures built and run, release workflows, English user/API/operations/development docs |

## Final verification

- `ruff check .`: passed.
- `ruff format --check .`: passed (37 Python files).
- Full suite with `RUN_E2E=1`, `HOOKROUTY_IMAGE=hookrouty:local`, `DOCKER_DEFAULT_PLATFORM=linux/amd64`: **57 passed** in 22.61 seconds, with no skips or warnings.
- Separate full container smoke test with `DOCKER_DEFAULT_PLATFORM=linux/arm64`: **1 passed** in 39.13 seconds under QEMU emulation.
- Final `docker buildx build --platform linux/amd64,linux/arm64 --load -t hookrouty:local .`: passed. Local multi-platform manifest digest at verification: `sha256:540c11f83d7da714e8ab949768a2b272e66308c7d49da9b8431a6a66df1fec9c`.
- `docker compose config --quiet` with valid test deployment variables: passed.
- Chromium journey and visual inspection at desktop 1440 px and mobile 390 px: passed. Screenshots are local ignored artifacts under test-results.
- Container tests verified actual service-to-service calls, raw gzip response headers/bytes, filtered cookies, restart persistence, backup creation, restore into a separate new volume, password recovery/session invalidation, non-root UID and read-only runtime, existing DB without bootstrap password and fresh DB refusal without it.
- Test containers/networks/volumes were cleaned up. Local built images and ignored development tools remain available.

## Review and fixes

One independent, read-only Superpowers review covered the full implementation. Its three material findings were each reproduced with a failing test, fixed and followed by the full passing suite:

1. Configured secret query values could be captured when echoed by the destination. The redactor now collects their rendered values and encoded forms.
2. Dot-only path substitutions could be normalized by HTTPX and change the destination path. They are now encoded, preserving the configured path structure.
3. Manual tests lacked a client-disconnect monitor. They now use the same receive-based monitor and proxy lifecycle as public hooks.

Other regression discoveries fixed during implementation: case-insensitive custom secret header masking, orphan reconciliation with zero active calls, JSON-object API validation, dynamically reassigned Docker test ports after restart, and invalid startup settings exposing the key in Pydantic error text.

## Decisions and verification limits

- Ruling: the workspace was empty and had no Git repository or remote. Initialize a local feature branch and keep it here. No existing code or branch was replaced; there is no base branch to merge into.
- Ruling: use `ghcr.io/<lowercase-owner>/<lowercase-repository>` derived at release time. This avoids inventing a registry namespace; publication still requires a real GitHub repository and an operator-created release.
- Ruling: keep the supplied Slovak plan intact for traceability and author all new documentation in English. Its original unchecked boxes are source text, not a live completion ledger; use this status file for verified results.
- Ruling: use one shared Jinja template and consolidate additional coverage in test_edges.py/test_routing.py rather than duplicating the plan's suggested page/test file layout. Public behavior and authorization stay shared. Tradeoff: larger individual files.
- Ruling: use one coherent initial implementation commit instead of nine intermediate commits, to keep this empty-project delivery economical. Tradeoff: coarser initial history.
- Ruling: v0.1 is the first database schema. Reopening/migration idempotence and backup restoration are verified; there is no genuine older application version to upgrade from. Future migrations need previous-version fixtures.
- Actual GitHub-hosted CI and GHCR publication have not been run because no remote repository is configured. The workflows are ready to run after push/release. No image or code was pushed externally.
- Review minor (deferred): admission capacity follows MAX_INFLIGHT_REQUESTS, but the explicitly constructed HTTPX transport retains its default connection-pool size (100). Values above 100 can wait inside HTTPX's pool within the total deadline. The default admission limit of 20 is unaffected. Use default-sized deployments until changing this advanced setting is accompanied by pool tuning.

The review set aside in-progress documentation and actual GitHub execution; documentation is now complete, while the external execution limitation remains explicit above. The reviewer did not rerun already verified browser/container suites; those were rerun by the implementer on the final image.

- Ruling: the configured GPG signer blocked the local commit waiting for unavailable interaction. Use an unsigned initial commit through a per-command override; global signing settings are unchanged. Tradeoff: this local commit has no cryptographic author signature.
