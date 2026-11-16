# Hook curl page implementation plan

Goal: make a copyable incoming webhook curl command available after creation and editing.

Design: add an authenticated, membership-scoped `/app/hooks/{id}/curl` page, linked from hook actions, the editor and test page. It consumes the existing safe hook data and APP_URL; the generator runs in the browser. Show method, public URL, route parameter inputs, an optional JSON request body and a token input. Empty token produces an explicit placeholder. A supplied token stays in page memory only. Existing tokens remain hash-only; a separate confirmed action can rotate through the existing CSRF-protected API and fill the newly generated token. Opening, copying or editing the example must not call the destination or mutate configuration.

Approach: reuse existing web authorization and plain JS/Jinja UI, without a new backend API, storage mechanism or dependencies. Persisting recoverable tokens would change the existing security contract, so the page accepts a saved token or generates a replacement with an explicit warning.

Constraints: English UI/docs; POSIX-shell quoting for every argument; correct incoming method and header/query authentication; encoded static/parameter path segments; HEAD uses `--head`; no automatic call, token rotation or browser storage. Read-only page works for disabled hooks, displays their state and rejects inaccessible/deleted hooks normally. Incoming tokens are distinct from destination credentials.

## Task 1: page, generator and documentation

- [x] Add web tests for authenticated access, scope, absence of tokens/destination credentials and no mutation/network activity; observe the missing-page failure.
- [x] Add browser tests for navigation after edit, route parameters, both auth modes, token placeholder/paste/confirmed rotation, clipboard fallback, safe quoting, HEAD and a real generated-command roundtrip; observe failures before implementation.
- [x] Add `templates/curl.html` and `static/curl.js`; integrate web route, conditional script, page bootstrap and links. Existing token rotation remains explicit and serialized.
- [x] Update user/template docs with token recovery limits and how to return to the curl page.
- [x] Run Ruff, full browser/unit/container suite, multi-platform build and one independent final read-only review; fix material findings with regression coverage.
- Publication: commit and integrate to main under the standing publication instruction; verify GitHub CI/GHCR.

## Ledger

- Started from clean main 6914b17, using feature branch codex/hook-curl-page in the existing checkout and development environment, consistent with the prior economical workflow.

- Baseline: 100 unit/integration tests passed before editing. New web and browser tests first failed on the absent page/link. Both now pass; browser coverage runs a generated command against a local destination and verifies safe quoting, copying/fallback, no automatic call, confirmed/cancelled rotation, invalid old token, refresh clearing and current HEAD/header settings.
- Final suite excluding containers: 106 passed, 1 container test deselected, in 82.90 s. Separate Docker smoke: amd64 1 passed in 16.47 s; arm64 1 passed in 81.12 s under QEMU. Local multi-platform build, Compose validation, Ruff lint/format (46 Python files), diff whitespace and UTF-8 checks passed.
- One independent read-only review found no important correctness/security issue. Mobile layout visually inspected at 390 px. No dependencies, database migration or new API needed.
