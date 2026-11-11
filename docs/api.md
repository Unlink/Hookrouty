# Management API

The same API powers the browser UI. Base prefix: `/api`. Requests and responses use JSON. All responses carry a generated `X-Hookrouty-Request-Id`; caller-supplied IDs are not trusted. Administration uses a server-side `hookrouty_session` cookie, not the public webhook token.

## Authentication and CSRF

1. `GET /api/session` creates a 15-minute anonymous session when needed and returns `{"csrf_token":"...","user":null}`. Preserve its cookie.
2. `POST /api/login` with `{"username":"admin","password":"..."}` and `X-CSRF-Token`. Login rotates the session cookie and CSRF token. Read the new token from the response.
3. Send the cookie and `X-CSRF-Token` on every mutation, including logout, previews and tests. Reads require the cookie. Sessions expire after 24 hours; cookies are HttpOnly, SameSite=Lax and Secure when APP_URL uses HTTPS.

CSRF protection also applies to login. Authentication failures are generic. Five failures per 15 minutes, tracked independently by username and client IP, cause 429 with `Retry-After: 900`. Proxy-forwarded IP headers are not trusted by the runtime.

## Endpoints

| Method and path | Input / behavior |
| --- | --- |
| GET `/session` | Current principal and CSRF token, or anonymous session |
| POST `/login` | `username`, `password`; returns rotated session metadata |
| POST `/logout` | Invalidates current session; 204 |
| POST `/me/password` | `current_password`, `password`; invalidates all own sessions; 204 |
| GET `/users` | Admin only; array of id, username, role, active |
| POST `/users` | Admin: username, password, role (default user), active (default true); 201 |
| PATCH `/users/{id}` | Admin: role and/or active; invalidates sessions |
| POST `/users/{id}/password` | Admin: password; 204 |
| GET `/hooks` | Array of safe accessible hook configurations |
| POST `/hooks` | HookSpec below; 201, returns id, token and safe hook |
| GET `/hooks/{id}` | Safe hook with stable ID |
| PATCH `/hooks/{id}` | Partial HookSpec; nested objects merge, arrays replace |
| DELETE `/hooks/{id}` | Removes active config/secrets; retains tombstone/history; 204 |
| GET `/hooks/{id}/members` | Admin: returns user_ids, also for tombstones |
| PUT `/hooks/{id}/members` | Admin: `{"user_ids":["uuid"]}`; replaces membership |
| POST `/hooks/{id}/duplicate` | Required input_path, optional name; returns disabled copy and new token; 201 |
| POST `/hooks/{id}/rotate-token` | `{}` generates token; optional custom token; returns id and token |
| POST `/hooks/{id}/preview` | Sample data below; masked request, no network/history |
| POST `/hooks/{id}/test` | Same sample data; real single call, returns safe execution/status |
| GET `/executions` | Scoped paginated records; filters below |
| GET `/executions/{request_id}` | Scoped detail plus uncertain_result |
| GET `/stats` | Scoped aggregate counters and success statistics |

Hook and history operations return 404 to unassigned members. Administrator operations return 403 to members. API access without an authenticated session returns 401. New resources return 201; logout/delete/password operations return 204. Invalid input returns 400. Route/username conflicts and last-admin protection return 409.

## HookSpec

```json
{
  "name": "Deploy application",
  "description": "Call the deployment service",
  "enabled": true,
  "input_method": "POST",
  "input_path": "/hooks/deploy/:project/:environment",
  "auth": {"mode": "header"},
  "target": {
    "method": "POST",
    "url": "http://deployment-service:8000/apps/{{path.project}}/deploy?fixed=1",
    "query": [{"name":"environment","value":"{{path.environment}}","secret":false}],
    "headers": [{"name":"Authorization","value":"Bearer replace-me","secret":true}],
    "body": {"mode":"json","template":{"version":"{{query.version}}","force":"{{body.force}}"},"secret":false}
  },
  "timeout_seconds": 60,
  "capture_bodies": false,
  "secret_refs": ["body.password"]
}
```

Name is 1–120 characters, description up to 2000. enabled defaults false. Methods are GET, POST, PUT, PATCH, DELETE, HEAD. Timeout is an integer 1–900, default 60. Header/query lists support up to 100 fields; names up to 200 characters. target.url is at most 8192 characters. Additional secret references support up to 100 entries. Body modes are none, passthrough, text, json. See [template rules](user-guide.md#destination-and-templates).

Creation returns `{"id":"uuid","token":"one-time-token","hook":{...}}`. A safe secret field reads as `{"id":"row-uuid","name":"Authorization","value":null,"secret":true,"configured":true,"value_action":"keep"}`. Reuse its ID and send `value_action: keep`, `replace` with a new value, or `remove`. A secret body uses the same action vocabulary with `template` instead of `value`. Ordinary PATCH responses return the safe hook directly. ID, token hash and encrypted ciphertext are never editable configuration fields.

## Preview and manual test samples

```json
{
  "path": {"project":"my-api","environment":"production"},
  "query": [["version","1.2.3"]],
  "headers": [["Content-Type","application/json"]],
  "body": "{\"force\":true}"
}
```

All sample fields default empty. Body is UTF-8 text. Duplicate query values retain their first value for references. Preview returns method, safe URL, masked headers, body text/omission reason and secret_fields. Manual test returns `request_id`, destination `status_code`, safe `execution` and `history_status`. Destination errors such as 500 remain status metadata in the test response; local proxy failures return their documented HTTP error. The public endpoint forwards the actual destination response body/status.

## History and statistics

`GET /executions` accepts `hook_id`, `outcome`, `source`, `since`, `until`, `page` (default 1), `page_size` (1–100, default 25). Times are ISO 8601 timestamps with a timezone. Response: `{"items":[...],"total":0,"page":1,"page_size":25}`. Records contain request_id, hook_id, hook_name_snapshot, source, attempted_target, started_at, finished_at, outcome, upstream_status, duration_ms and safe_snapshot.

`GET /stats` accepts `period=24h|7d` (default 24h) and hook/source/outcome filters. It returns calls, completed_attempts, successes, success_rate (0–1 or null), average_duration_ms (or null), running, rejected and per-outcome counts. Admin responses also include unknown_routes. All SQL filters enforce current membership, including for deleted-hook history.

## Public errors

Local router errors use this safe shape, never raw exceptions:

```json
{"error":{"code":"UPSTREAM_TIMEOUT","message":"Destination did not respond before the timeout; the remote action may have completed."},"request_id":"uuid"}
```

| HTTP | Code | Meaning |
| --- | --- | --- |
| 400 | INVALID_INPUT / TEMPLATE_ERROR | Invalid path, configuration or template data |
| 401 | INVALID_TOKEN | Missing or incorrect webhook token |
| 404 | HOOK_NOT_FOUND | Unknown, deleted or disabled route |
| 405 | METHOD_NOT_ALLOWED | Existing path, unsupported method |
| 413 | PAYLOAD_TOO_LARGE | Incoming or rendered body exceeds limit |
| 499 | CLIENT_DISCONNECTED | Client disconnected; usually no recipient remains |
| 502 | UPSTREAM_CONNECTION_ERROR | Network/TLS/protocol failure; remote action may have happened |
| 502 | UPSTREAM_RESPONSE_TOO_LARGE | Destination response exceeds transfer limit |
| 503 | CAPACITY_EXCEEDED | Immediate rejection; no queued destination attempt |
| 503 | STORAGE_UNAVAILABLE | Initial execution could not be committed; target not contacted |
| 504 | UPSTREAM_TIMEOUT | Total network deadline expired; no retry |

Every public HEAD response has no body. If final history storage fails after a destination response, the real response is returned with `X-Hookrouty-History-Status: unavailable`. The request is never retried. A later maintenance pass marks orphaned running records interrupted.
