# Webhook setup templates

The **Create hook** wizard builds an ordinary Hookrouty hook from a reusable definition. Templates are stored in SQLite and can be added without rebuilding the image. They are available to all signed-in users when enabled. Administrators manage definitions under **Templates**; members cannot change them.

## Create a simple webhook

1. Open **Hooks → Create hook**, enter a name and select a template.
2. Fill the destination fields. Addresses must be reachable from the Hookrouty container. Use secret fields for destination credentials.
3. Review the masked destination request, keep or change the generated incoming route, and choose caller authentication. Header authentication is the default; secret-URL authentication puts the token in a `token` query parameter.
4. Select **Create webhook**. Copy the URL, token or ready-to-use curl command from the result. The token is displayed only once and is stored only as a hash. The curl invokes the real action; copying it does not.

Preview and creation never contact the destination or create an execution record. Created hooks use incoming POST, body capture off, normal per-hook access and encrypted destination secrets. They are enabled by default; uncheck the switch during review to create a disabled hook. The creator receives access automatically. Use **Advanced settings** after creation for request-dependent templates, another incoming method, body capture or assignments. The complete editor is also available through **Advanced editor** before creation.

Copy buttons support browsers with clipboard access and provide a selection-based fallback. Keep copied tokens and URLs containing tokens private. Configure `APP_URL` to the public address used by callers so the generated URLs are correct. After leaving the result, open **cURL** from the hook list/editor/test page to generate a fresh example from the saved settings. Paste your saved incoming token; if it is lost, the page offers explicit confirmed rotation. See [copying commands after editing](user-guide.md#copy-a-curl-command-after-editing).

## Built-in templates

**Simple HTTP URL** calls a destination URL using POST by default, or GET, PUT, PATCH, DELETE or HEAD. An optional Bearer token becomes an encrypted Authorization header. With an empty token no Authorization header is configured. No body is sent. Credentials in URL userinfo are rejected; place query credentials in a custom template's secret query input.

**Dockhand redeploy** asks for the Dockhand base URL, stack name, optional environment ID and a Dockhand API token. It sends:

```http
POST /api/stacks/{encoded-stack-name}/deploy?env={environment-id}
Authorization: Bearer <Dockhand API token>
Content-Type: application/json

{"pull":true,"build":false,"forceRecreate":true}
```

An empty environment omits `env`. The timeout is 300 seconds. The preset omits `Accept: application/json` to start Dockhand's asynchronous deployment job. A successful Hookrouty execution means Dockhand accepted the request; check job/deployment completion in Dockhand. This supports an existing Compose/Internal stack and does not synchronize a Git-managed stack. Use an appropriate Dockhand token and a stack the token can deploy. See the [Dockhand API reference](https://dockhand.pro/manual/#api-reference).

## Manage definitions

In **Templates**, use **Copy** on a definition or **New template**. Enter the display name, description, enabled state, timeout, input-field JSON array and destination-mapping JSON object. Save to make the definition available immediately. Names are unique ignoring case. Disable a template to hide it from members and prevent new preview/creation; administrators can still edit it. Deleting, disabling or changing a template never changes hooks already created from it. Those hooks contain independent compiled configuration, not a live reference.

The two built-ins are seeded once during migration/startup. Editing or deleting them is persistent; restarting does not restore deleted definitions. Templates are included in the normal database backup and restore.

Example fields:

```json
[
  {"key":"url","label":"Service URL","kind":"url","required":true},
  {"key":"project","label":"Project","kind":"text","required":true},
  {"key":"token","label":"API token","kind":"secret","required":true},
  {"key":"environment","label":"Environment","kind":"select","options":["staging","production"],"default":"staging"}
]
```

Example destination mapping:

```json
{
  "method":"POST",
  "url":"{{inputs.url}}/projects/{{inputs.project}}/deploy",
  "headers":[{"name":"Authorization","value":"Bearer {{inputs.token}}"}],
  "query":[{"name":"environment","value":"{{inputs.environment}}"}],
  "body":{"mode":"json","template":{"pull":true}}
}
```

Input keys are unique ASCII identifiers, up to 64 characters. At most 32 inputs are supported. Each field has a `key`, `label`, `kind` (`text`, `url`, `secret`, `select`, `json`), optional `help`, `required` (default true), and optional `default`. Select fields require unique `options`; secret fields cannot have defaults. JSON fields preserve JSON value types when referenced as a whole. Optional fields with no value become empty text or JSON null.

Only `{{inputs.key}}` references are accepted in mapping values. References are resolved once when the hook is created, not on public calls. There is no code execution, expression evaluation, filesystem access, loop or remote template loading. Input values are literals and cannot contain runtime `{{...}}` expressions; use the advanced editor for those. Object keys remain fixed. Nesting is limited to 32 levels.

A URL input can provide the whole URL or the leading base URL. When adding a path suffix, the base must have no query or fragment. Other URL substitutions are encoded as individual path values; references in a literal URL authority are rejected. URL credentials and fragments are rejected. Header/query names are fixed; mapped values follow the ordinary hook validation rules. Set `omit_if_empty: true` on a header/query entry to omit it when a referenced input is empty.

Secret input references automatically mark their destination header, query or entire body as secret, preserving normal encrypted storage and masking. Secret inputs cannot be interpolated into the destination URL. Known sensitive query keys in literal URLs, URL defaults and supplied URLs are rejected; configure them as separate secret query fields instead. Known sensitive header/query/body names require secret inputs rather than literal credentials. Shared definitions, descriptions, defaults and ordinary text are readable by users: never paste credentials into them. For arbitrary confidential JSON supplied through a `json` field, set the mapped body to `"secret":true`; the entire body is then encrypted. A marked secret body can reference only secret or JSON inputs without defaults. Hookrouty cannot infer every application-specific secret.

Body modes are `none`, `passthrough`, `text`, `json`; GET/HEAD require `none`. Passthrough forwards the future caller's bytes, so the setup preview has an empty body. Timeout is 1–900 seconds. Public-call limits, no automatic retries and uncertain remote results retain their ordinary behavior.

## API

All endpoints require a session; all POST/PUT/DELETE requests also require CSRF. Definition mutations require an administrator.

| Method and path | Behavior |
| --- | --- |
| GET `/api/templates` | Definitions; members see enabled templates only |
| GET `/api/templates/{id}` | One visible definition |
| POST `/api/templates` | Create definition; returns definition including id; 201 |
| PUT `/api/templates/{id}` | Replace full definition; 200 |
| DELETE `/api/templates/{id}` | Delete definition; 204 |
| POST `/api/templates/{id}/preview` | Compile and return masked request plus generated input_path; no network/history |
| POST `/api/templates/{id}/hooks` | Compile and create ordinary hook; returns id, one-time token, safe hook; 201 |

Definition JSON combines `name`, `description`, `enabled`, `fields`, `target`, `timeout_seconds`. Preview/create input:

```json
{
  "name":"Deploy service",
  "values":{"url":"http://service:8000","project":"api","token":"private-token","environment":"staging"},
  "input_path":"/hooks/deploy-service",
  "auth_mode":"header",
  "enabled":true
}
```

Omit `input_path` for a generated route; reuse the preview's `input_path` at creation to retain it. Unknown input keys, missing required values, invalid choices or mappings return 400. Duplicate template names or conflicting hook routes return 409. Disabled or unavailable templates return 404 for preview/create. Normal hook permissions apply after creation.
