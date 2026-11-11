# User guide

## Accounts and assignments

Administrators manage accounts in **Users**: create a username/password, choose `admin` or `user`, change role/active state, or reset a password. Usernames are case-insensitive and contain 3–64 ASCII letters, digits, dots, hyphens or underscores. Passwords contain 8–1024 characters. Deactivation, role updates and password changes invalidate sessions. The last active administrator cannot be demoted or deactivated. Change your own password under **Profile** using the current password.

A member can create hooks and automatically receives access. Assigned members can edit, enable/disable, duplicate, test, rotate tokens and delete their hooks. Administrators can change assignments in the hook editor. Removing an assignment takes effect on the next operation and also removes access to historical records. Deleted hooks retain only a tombstone and memberships for access to retained history; active configuration, token hash and encrypted fields are removed.

## Incoming routes

Public endpoints live under `/hooks/`; administration lives under `/app/` and `/api/`. A public `GET /hooks/new` is independent of the editor `/app/hooks/new`.

Choose one incoming method: GET, POST, PUT, PATCH, DELETE or HEAD. Parameters use `:name` and match exactly one nonempty segment. Names must be unique and match `[A-Za-z_][A-Za-z0-9_]*`. Static segments take precedence over parameters at the same position, comparing from left to right. Equal-priority overlapping patterns are rejected. Method participates in routing. `/hooks/example` and `/hooks/example/` differ; no slash redirects are applied.

Paths are decoded exactly once. Encoded slash, backslash, NUL, invalid UTF-8 and malformed percent escapes are rejected. `%252F` remains the parameter value `%2F`. Disabled hooks return 404. Existing paths with a different method return 405. Tokens are checked after selecting a single route; authentication failure never tries a less-specific hook.

Each hook requires a token, independent of login cookies. Use `X-Webhook-Token` or select the query parameter `token`. Header authentication avoids putting credentials in URLs. A random 256-bit token is generated unless a custom nonempty token is supplied. Copy generated tokens at creation/rotation: only their hashes are stored. Rotating a token invalidates the previous one immediately. Duplicating a hook requires a new route, generates a new token and leaves the copy disabled.

## Destination and templates

Choose an independent target method and HTTP(S) URL. Internal LAN/Docker addresses are allowed. Scheme, hostname and port are fixed configuration. Userinfo and URL fragments are forbidden. Templates belong in the URL path, separately configured query values, header values and bodies. Query fields preserve order; existing fixed URL query parameters remain before the configured fields. Header names are unique ignoring case. Host, Content-Length and hop-by-hop headers are reserved.

Supported references:

| Reference | Source |
| --- | --- |
| `{{path.project}}` | Incoming route parameter |
| `{{query.version}}` | First value of a query parameter |
| `{{headers.X-Event}}` | Case-insensitive incoming header |
| `{{body.release.tag}}` | Nested JSON object key |

There are no expressions, filters, loops, array indexes, dotted object keys, Jinja execution or Python evaluation. A missing value, required invalid JSON, incompatible object-to-text interpolation or invalid rendered header returns 400 before contacting the destination. URL substitutions are encoded as individual path segments; query values use query encoding. CR/LF is forbidden in outgoing headers.

Body modes:

| Mode | Behavior |
| --- | --- |
| `none` | Empty body; required for GET/HEAD destinations |
| `passthrough` | Original bytes; preserves incoming Content-Type unless explicitly configured |
| `text` | UTF-8 text with scalar substitutions |
| `json` | Structured JSON template with safe serialization |

A standalone JSON string `"{{body.enabled}}"` becomes a JSON boolean when the source is boolean. Embedded `"Enabled: {{body.enabled}}"` becomes text. Objects/arrays are allowed as standalone JSON references, but cannot be automatically embedded into text. Only explicitly configured headers are sent, plus HTTP protocol defaults; incoming authorization and webhook tokens are never automatically forwarded.

## Secrets

Mark header/query fields **Secret** to encrypt them at rest and hide their values in reads and previews. Existing secret fields have explicit **Keep**, **Replace** and **Remove** actions. Keep preserves the row ID, name, secret flag and stored value. Mask text is never used as a credential. The entire configured body can be marked secret, encrypted and excluded from capture. Put credentials in separate secret query fields rather than a literal URL.

Authorization, Proxy-Authorization, Cookie, Set-Cookie, incoming token, configured secret fields and common sensitive JSON keys are masked in snapshots. Add references such as `query.api_key`, `headers.X-Private` or `body.customer.password` for application-specific secrets. If a secret reference participates in a template, the entire resulting URL, header or body field is hidden. Recognized secret values are also removed from captured echoes. Arbitrary secrets typed into names, descriptions, literal URLs or ordinary text cannot be inferred reliably; use the dedicated secret controls.

## Preview and test

Open **Test** from the hook list. Supply path parameters as a JSON object, query/headers as ordered JSON pairs, and body as UTF-8 text. **Preview only** uses the production renderer, returns a safe snapshot, creates no execution and never contacts the destination. **Send real test** sends one real request, including for a disabled hook, with `source=manual_test`. It does not bypass membership checks. Results are displayed as text, never rendered as destination HTML.

## Activity

Filter by hook, last 24 hours/7 days, outcome and source. Lists paginate 25 records by default; the API supports up to 100. The page polls every five seconds and pauses while hidden. Times display in the browser's local timezone; storage and API use UTC.

Outcomes: `running`, `success` (2xx), `redirected` (3xx), `upstream_error` (4xx/5xx), `rejected` (local validation/auth/capacity), `proxy_error`, `timeout`, `client_disconnected`, `interrupted`. Success rate uses completed destination attempts; local rejection, running and uncertain interrupted/disconnected calls are excluded. Timeouts and connection failures after the attempt count as failed attempts. Unknown public paths increment an administrator-only daily counter instead of individual execution rows.

Capture is off by default. When enabled, incoming, rendered and response text/JSON bodies are masked before truncation, with a default 16 KiB per body. Binary, encrypted secret and compressed bodies are omitted with a reason. History truncation never truncates the caller's response. Execution detail shows `uncertain_result` when no reliable remote outcome was obtained. No outcome causes an automatic retry.
