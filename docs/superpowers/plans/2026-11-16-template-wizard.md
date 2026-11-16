# Template-driven webhook wizard

User requirement: simplify hook creation with a wizard, including Simple HTTP URL and Dockhand redeploy presets, and let administrators add templates dynamically without code changes.

## Design
- `/app/hooks/new` becomes a three-step wizard: choose template/name, fill template fields, review/create. `/app/hooks/new?mode=advanced` preserves the current full editor. Existing-hook editing remains advanced.
- Templates are global non-secret definitions stored in SQLite, editable by administrators through `/app/templates` and `/api/templates`. Signed-in members can use enabled templates. Definitions resolve into the existing HookSpec and save_hook authorization/encryption pipeline.
- Seed Simple HTTP URL and Dockhand redeploy once. Changes/deletions survive restarts; deleting a template never changes existing hooks.
- Fields support text, URL, secret, choice and JSON values. Declarative `{{inputs.key}}` references are resolved only during configuration. No expressions, executable plugins, secret defaults or secret values in destination URLs. Header/query/body references to secret fields automatically become encrypted hook fields.
- Preview uses the production renderer/redaction with no network request/history. Creation itself never calls the destination. Final step shows the one-time token and ready-made curl; copies remain in page memory only. Incoming POST and header-token auth are defaults. Users can explicitly choose a token-bearing URL. Body capture stays disabled.

## Plan and verification
1. Add focused template/schema/permission/seed/preview tests and observe failures.
2. Add migration 2, typed definitions/input validation, restricted rendering and admin/use API; test materialized HookSpec encryption and isolation.
3. Add template-driven wizard and admin definition editor using existing API/session/CSRF/CSP. Keep advanced form available. Browser coverage proves simple and Dockhand creation, custom template availability, masked preview, no unintended destination call, back navigation and narrow layout.
4. Update English docs, run Ruff/unit/browser/Docker verification, one independent review, merge to main and push through existing verified GHCR workflow.

## Ledger
- Started from clean main bc0493a; feature branch feat/template-wizard.
- Added migration 2, seeded definitions, admin CRUD and masked preview/materialization endpoints. Focused tests were first run failing, then passed after implementation.
- Added the three-step wizard and JSON definition editor. Chromium covers the built-ins, an administrator-created template, back navigation, both caller authentication modes, clipboard and fallback copying, masked secrets, mobile layout and an explicit destination roundtrip. Existing advanced-editor journeys still pass.
- Independent read-only review found sensitive URL query values bypassing secret storage and secret bodies accepting credential defaults. Five regression cases reproduced both findings before fixes. URL secrets now require dedicated secret query inputs; marked secret bodies require secret/JSON inputs without defaults.
- Added invalid-default-type coverage, confidential JSON-body encryption and optional Authorization omission. Final focused template suite: 24 cases.
- Final verification: Ruff lint/format passed; full suite with Chromium and amd64 Docker: 105 passed in 42.52 s; arm64 container smoke under QEMU: 1 passed in 41.20 s; Compose validation passed.
- Multi-platform local build passed. Container verification includes template-created real HTTP calls, custom template/hook persistence, backup/restore, non-root/read-only runtime and existing proxy regressions.
- English wizard/template schema/API documentation added and linked from README/user/API guides; operations and architecture describe migration/materialization.
