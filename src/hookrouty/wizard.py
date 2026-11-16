"""Declarative management-time templates. Definitions never execute code."""

import json
import re
import sqlite3
from typing import Any, Literal
from urllib.parse import parse_qsl, quote, urlsplit
from uuid import uuid4

from pydantic import Field, model_validator

from .models import HookSpec, Model, RouterError, TargetSpec, TemplateContext
from .redaction import known_secrets, rendered_snapshot, scrub_known
from .security import now
from .templating import SENSITIVE, render_request

REF = re.compile(r"{{\s*inputs\.([A-Za-z_][A-Za-z0-9_]*)\s*}}")


def check_url_secrets(value):
    if any(key.lower() in SENSITIVE for key, _ in parse_qsl(urlsplit(value).query)):
        raise ValueError("Put URL credentials in a separate secret query input")


class InputField(Model):
    key: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=64)
    label: str = Field(min_length=1, max_length=120)
    kind: Literal["text", "url", "secret", "select", "json"] = "text"
    required: bool = True
    default: Any = None
    options: list[str] = Field(default_factory=list, max_length=32)
    help: str = Field("", max_length=500)

    @model_validator(mode="after")
    def valid(self):
        if self.kind != "json" and self.default is not None and not isinstance(self.default, str):
            raise ValueError("Text input defaults must be strings")
        if self.kind == "secret" and self.default is not None:
            raise ValueError("Secret inputs cannot have defaults")
        if self.kind == "select" and (
            not self.options or len(set(self.options)) != len(self.options)
        ):
            raise ValueError("Choice inputs require unique options")
        if self.kind == "select" and self.default is not None and self.default not in self.options:
            raise ValueError("Default must be an available choice")
        if self.kind == "url" and self.default is not None:
            check_url_secrets(self.default)
        return self


class TemplateParameter(Model):
    name: str = Field(min_length=1, max_length=200)
    value: str
    secret: bool = False
    omit_if_empty: bool = False


class TemplateBody(Model):
    mode: Literal["none", "passthrough", "text", "json"] = "none"
    template: Any = None
    secret: bool = False


class TemplateTarget(Model):
    method: str = "POST"
    url: str = Field(min_length=1, max_length=8192)
    query: list[TemplateParameter] = Field(default_factory=list, max_length=100)
    headers: list[TemplateParameter] = Field(default_factory=list, max_length=100)
    body: TemplateBody = Field(default_factory=TemplateBody)


class WizardTemplateSpec(Model):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=2000)
    enabled: bool = True
    fields: list[InputField] = Field(default_factory=list, max_length=32)
    target: TemplateTarget
    timeout_seconds: int = Field(60, ge=1, le=900)

    @model_validator(mode="after")
    def valid_definition(self):
        fields = {field.key: field for field in self.fields}
        if len(fields) != len(self.fields):
            raise ValueError("Input keys must be unique")
        refs_in(self.target.model_dump(), set(fields))
        if any(fields[key].kind == "secret" for key in refs_in(self.target.url, set(fields))):
            raise ValueError("Put credentials in secret headers, query fields or bodies")
        for item in self.target.headers + self.target.query:
            refs = refs_in(item.value, set(fields))
            if item.secret or item.name.lower() in SENSITIVE:
                if not refs or any(fields[key].kind != "secret" for key in refs):
                    raise ValueError("Credentials must come from secret inputs")
        if self.target.body.secret:
            refs = refs_in(self.target.body.template, set(fields))
            if not refs or any(
                fields[key].kind not in {"secret", "json"} or fields[key].default is not None
                for key in refs
            ):
                raise ValueError("Secret bodies require secret or JSON inputs without defaults")

        def check_body(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key.lower() in SENSITIVE:
                        refs = refs_in(child, set(fields))
                        if not refs or any(fields[ref].kind != "secret" for ref in refs):
                            raise ValueError("Sensitive body fields must come from secret inputs")
                    check_body(child)
            elif isinstance(value, list):
                for child in value:
                    check_body(child)

        check_body(self.target.body.template)
        values = {
            field.key: field.default
            if field.default is not None
            else "https://template.invalid"
            if field.kind == "url"
            else field.options[0]
            if field.kind == "select"
            else {}
            if field.kind == "json"
            else "sample"
            for field in self.fields
        }
        compile_target(self, values)
        return self


class WizardDraft(Model):
    name: str = Field(min_length=1, max_length=120)
    values: dict[str, Any] = Field(default_factory=dict)
    input_path: str | None = None
    auth_mode: Literal["header", "query"] = "header"
    enabled: bool = True


def refs_in(value, keys, depth=0):
    if depth > 32:
        raise ValueError("Template nesting exceeds 32 levels")
    if isinstance(value, str):
        if "{{" in REF.sub("", value) or "}}" in REF.sub("", value):
            raise ValueError("Only inputs.key references are supported")
        refs = {m.group(1) for m in REF.finditer(value)}
        if not refs <= keys:
            raise ValueError("Unknown template input")
        return refs
    result = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if "{{" in key or "}}" in key:
                raise ValueError("Object keys must be fixed")
            result.update(refs_in(child, keys, depth + 1))
    elif isinstance(value, list):
        for child in value:
            result.update(refs_in(child, keys, depth + 1))
    return result


def scalar(value):
    if isinstance(value, (dict, list)):
        raise ValueError("Objects and arrays require a standalone JSON reference")
    return "" if value is None else json.dumps(value) if isinstance(value, bool) else str(value)


def fill(value, values, encode=False):
    if isinstance(value, str):
        match = REF.fullmatch(value)
        if match and not encode:
            return values[match.group(1)]
        return REF.sub(
            lambda m: (
                quote(scalar(values[m.group(1)]), safe="") if encode else scalar(values[m.group(1)])
            ),
            value,
        )
    if isinstance(value, dict):
        return {key: fill(child, values) for key, child in value.items()}
    if isinstance(value, list):
        return [fill(child, values) for child in value]
    return value


def compile_target(template, values):
    keys = {field.key for field in template.fields}
    secret_keys = {field.key for field in template.fields if field.kind == "secret"}
    pattern = template.target.url
    prefix = REF.match(pattern)
    url_fields = {field.key for field in template.fields if field.kind == "url"}
    if prefix and prefix.group(1) in url_fields:
        base = str(values[prefix.group(1)])
        suffix = pattern[prefix.end() :]
        parts = urlsplit(base)
        if suffix and (parts.query or parts.fragment or not suffix.startswith("/")):
            raise ValueError("Base URL must not have query or fragment")
        url = (base.rstrip("/") if suffix else base) + fill(suffix, values, encode=True)
    else:
        authority = urlsplit(pattern).netloc
        if REF.search(authority):
            raise ValueError("Use a URL input for the whole base URL")
        url = fill(pattern, values, encode=True)
    check_url_secrets(url)

    def parameters(items):
        result = []
        for item in items:
            refs = refs_in(item.value, keys)
            if item.omit_if_empty and any(values[key] is None or values[key] == "" for key in refs):
                continue
            result.append(
                {
                    "name": item.name,
                    "value": scalar(fill(item.value, values)),
                    "secret": item.secret
                    or item.name.lower() in SENSITIVE
                    or bool(refs & secret_keys),
                }
            )
        return result

    body = template.target.body
    body_value = fill(body.template, values)
    target = {
        "method": scalar(fill(template.target.method, values)),
        "url": url,
        "query": parameters(template.target.query),
        "headers": parameters(template.target.headers),
        "body": {
            "mode": body.mode,
            "template": body_value,
            "secret": body.secret or bool(refs_in(body.template, keys) & secret_keys),
        },
    }
    return TargetSpec.model_validate(target)


def prepare_spec(template, draft):
    fields = {field.key: field for field in template.fields}
    if not set(draft.values) <= set(fields):
        raise RouterError(400, "INVALID_INPUT", "Unknown template input.")
    values = {}
    for key, field in fields.items():
        value = draft.values.get(key, field.default)
        if field.required and (value is None or value == ""):
            raise RouterError(400, "INVALID_INPUT", "Complete all required template fields.")
        if field.kind != "json":
            if value is None:
                value = ""
            if not isinstance(value, str):
                raise RouterError(400, "INVALID_INPUT", "Template text fields require strings.")
            if field.kind == "select" and value not in field.options:
                raise RouterError(400, "INVALID_INPUT", "Choose a valid template option.")
            if field.kind == "url" and value:
                url = urlsplit(value)
                if (
                    url.scheme not in {"http", "https"}
                    or not url.hostname
                    or url.username is not None
                    or url.password is not None
                    or url.fragment
                ):
                    raise RouterError(
                        400, "INVALID_INPUT", "Use an HTTP(S) URL without credentials or fragment."
                    )
        # Input values are literals, not a second executable/template language.
        pending = [(value, 0)]
        while pending:
            item, depth = pending.pop()
            if depth > 32:
                raise RouterError(400, "INVALID_INPUT", "Input JSON nesting exceeds 32 levels.")
            if isinstance(item, str) and ("{{" in item or "}}" in item):
                raise RouterError(
                    400, "INVALID_INPUT", "Use the advanced editor for runtime request templates."
                )
            if isinstance(item, dict):
                pending.extend((child, depth + 1) for pair in item.items() for child in pair)
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)
        values[key] = value
    slug = re.sub(r"[^a-z0-9]+", "-", draft.name.lower()).strip("-")[:60] or "webhook"
    return HookSpec(
        name=draft.name,
        input_path=draft.input_path or "/hooks/" + slug + "-" + uuid4().hex[:8],
        enabled=draft.enabled,
        auth={"mode": draft.auth_mode},
        target=compile_target(template, values),
        timeout_seconds=template.timeout_seconds,
        capture_bodies=False,
    )


def preview_spec(spec, limit):
    context = TemplateContext({}, {}, {}, None, set())
    rendered = render_request(spec, context, b"", None)
    secrets = known_secrets(context, rendered, spec)
    return scrub_known(
        rendered_snapshot(rendered, spec, limit, preview=True, known_values=secrets), secrets
    )


SIMPLE = {
    "name": "Simple HTTP URL",
    "description": "Call a URL with an optional Bearer token. No request body or expressions required.",
    "fields": [
        {
            "key": "url",
            "label": "Destination URL",
            "kind": "url",
            "help": "Use an address reachable from the Hookrouty container.",
        },
        {
            "key": "method",
            "label": "HTTP method",
            "kind": "select",
            "options": ["POST", "GET", "PUT", "PATCH", "DELETE", "HEAD"],
            "default": "POST",
        },
        {"key": "token", "label": "Bearer token (optional)", "kind": "secret", "required": False},
    ],
    "target": {
        "method": "{{inputs.method}}",
        "url": "{{inputs.url}}",
        "headers": [
            {"name": "Authorization", "value": "Bearer {{inputs.token}}", "omit_if_empty": True}
        ],
    },
}
DOCKHAND = {
    "name": "Dockhand redeploy",
    "description": "Start an asynchronous image pull and recreation of a Compose/Internal stack. Check completion in Dockhand. Does not sync Git stacks.",
    "fields": [
        {
            "key": "url",
            "label": "Dockhand URL",
            "kind": "url",
            "help": "For example https://dockhand.example.com or a reachable private HTTP address.",
        },
        {"key": "stack", "label": "Stack name", "kind": "text"},
        {
            "key": "environment",
            "label": "Environment ID (optional)",
            "kind": "text",
            "required": False,
            "help": "Leave empty to use the default environment.",
        },
        {
            "key": "token",
            "label": "Dockhand API token",
            "kind": "secret",
            "help": "Create a token in your Dockhand profile. It is encrypted in the created hook.",
        },
    ],
    "target": {
        "method": "POST",
        "url": "{{inputs.url}}/api/stacks/{{inputs.stack}}/deploy",
        "query": [{"name": "env", "value": "{{inputs.environment}}", "omit_if_empty": True}],
        "headers": [{"name": "Authorization", "value": "Bearer {{inputs.token}}"}],
        "body": {"mode": "json", "template": {"pull": True, "build": False, "forceRecreate": True}},
    },
    "timeout_seconds": 300,
}


async def seed_templates(db):
    async with db.transaction() as connection:
        async with connection.execute(
            "SELECT value FROM app_meta WHERE key='wizard_templates_seeded'"
        ) as cursor:
            if await cursor.fetchone():
                return
        for tid, data in [("simple-http-url", SIMPLE), ("dockhand-redeploy", DOCKHAND)]:
            spec = WizardTemplateSpec.model_validate(data)
            await connection.execute(
                "INSERT INTO hook_templates VALUES(?,?,?,?,?,?)",
                (tid, spec.name, spec.model_dump_json(), spec.enabled, now(), now()),
            )
        await connection.execute("INSERT INTO app_meta VALUES('wizard_templates_seeded','1')")


async def template_row(db, tid, actor, usable=False):
    row = await db.one("SELECT * FROM hook_templates WHERE id=?", (tid,))
    if not row or ((usable or actor.role != "admin") and not row["enabled"]):
        raise RouterError(404, "TEMPLATE_NOT_FOUND", "Template not found.")
    return row


def template_read(row):
    return {"id": row["id"], **json.loads(row["definition_json"])}


async def save_template(db, spec, tid=None):
    tid = tid or str(uuid4())
    try:
        await db.execute(
            "INSERT INTO hook_templates VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,definition_json=excluded.definition_json,enabled=excluded.enabled,updated_at=excluded.updated_at",
            (tid, spec.name, spec.model_dump_json(), spec.enabled, now(), now()),
        )
    except sqlite3.IntegrityError:
        raise RouterError(
            409, "TEMPLATE_CONFLICT", "A template with this name already exists."
        ) from None
    return template_read(await db.one("SELECT * FROM hook_templates WHERE id=?", (tid,)))
