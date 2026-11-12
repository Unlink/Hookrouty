# Security Audit Remediation Plan

**Goal:** Correct all four confirmed Astra findings and apply the practical hardening recommendations without changing webhook transfer bytes or team authorization.
**Spec:** Astra read-only audit of f10b6f5 and docs/architecture.md. Execute inline, economically, with regression-first verification.

## Constraints and decisions
- Work on the existing clean feat/initial-implementation branch; it isolates this delivery from main.
- Keep source/UI/docs English. Preserve the single-worker, one-attempt proxy and existing API shapes.
- Add a separate MAX_MANAGEMENT_BODY_BYTES limit (1 MiB default) at the ASGI boundary, counting streamed bytes before framework parsing; management body reads have a fixed 15-second total deadline.
- Forward upstream bytes and status unchanged, but enforce a sandboxed, script-free opaque-origin document policy on hook responses. Replace upstream CSP/reporting directives so upstream content cannot opt out or create browser reports containing incoming credentials.
- Use SHA-pinned existing action versions and image manifests, plus a pinned build backend. No registry publication.

## Tasks
1. [x] tests/test_security.py: prove capture exceptions release capacity and invalid Unicode/deep bodies produce a safe result. Move snapshot construction inside proxy cleanup; make capture omissions safe.
2. [x] tests/test_security.py: streamed/declared oversized API bodies are rejected before full consumption for anonymous/authenticated callers; exact boundary passes. Implement management ASGI limit/deadline, expose configuration in Compose/docs.
3. [x] tests/test_security.py: nested array secrets propagate to preview/history; plaintext, percent-encoded and form-encoded echoes are masked in headers and bodies before truncation. Make discovery and scalar collection consistent; preserve types in transmitted JSON.
4. [x] tests/test_security.py: literal/adjacent/empty substitutions cannot create normalized dot segments in the final HTTPX URL. Encode complete rendered dot segments while preserving ordinary paths.
5. [x] Add browser coverage for script-free public documents and opaque origin; enforce CSP without altering transferred bytes/status. Bound anonymous-session storage at 1000 unexpired anonymous rows atomically; permit existing sessions and reclaim expired rows. Tune the HTTPX transport pool to configured admission capacity.
6. [x] Pin all workflow action references and Docker source manifests from verified upstream refs; pin Hatchling and refresh uv.lock. Document audit resolution and deployment behavior.
7. [x] Run focused RED/GREEN cycles, full tests including Chromium, Ruff, locked dependency sync, Compose validation, and final Docker smoke tests on amd64 and arm64. One independent read-only review, fix material findings, commit and push to the already authorized implementation branch.

## Review focus
- Exceptions or cancellation between slot reservation and the first storage write must release capacity.
- API bodies without Content-Length, misleading lengths and slow reads must remain bounded.
- Secret values inside nested arrays, escaped Unicode and query encoding must not survive stored captures or previews.
- Final request URLs, not only individual substitutions, must preserve path segment boundaries.
- Upstream HTML cannot execute on the management origin, including when upstream supplies permissive CSP/reporting headers; normal webhook bytes and existing UI remain intact.

## Ledger
- Started from clean f10b6f5. No unrelated user changes.

- Completed tasks 1–7 locally: regression failures observed, all fixes green, 79 full tests plus separate arm64 smoke passed, lint/format/Compose passed, and Astra returned no material blocker. Source/runtime build pins were verified upstream; uv.lock remained unchanged after refresh.
- Ruling: enforce CSP sandbox rather than requiring a second hostname, preserving the existing one-container deployment. Browser tests verify opaque origin and script/storage denial; operators can additionally isolate hostnames.
- Review minor (deferred): per-source anonymous issuance throttling/reserved bootstrap capacity is additional ingress hardening. The global storage budget is implemented; existing sessions are unaffected by a full budget.
- Integration: commit and push to the previously authorized feature branch after recording this verification. No release, merge or registry push.
