import json
import re
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from .models import RenderedRequest, RouterError, TemplateContext

REFERENCE = re.compile(
    r"{{\s*((?:path|query|headers|body)\.[A-Za-z0-9_!-]+(?:\.[A-Za-z0-9_!-]+)*)\s*}}"
)
SENSITIVE = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-webhook-token",
    "token",
    "password",
    "passwd",
    "secret",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "client_secret",
}


def validate_templates(value):
    if isinstance(value, str):
        residue = REFERENCE.sub("", value)
        if "{{" in residue or "}}" in residue:
            raise ValueError("Only simple path, query, headers and body references are supported")
    elif isinstance(value, dict):
        for key, child in value.items():
            if "{{" in key or "}}" in key:
                raise ValueError("Template object keys must be fixed")
            validate_templates(child)
    elif isinstance(value, list):
        for child in value:
            validate_templates(child)


def build_context(request, path, secret_refs):
    query, headers = {}, {}
    for key, value in request.query:
        query.setdefault(key, value)
    for key, value in request.headers:
        headers.setdefault(key.lower(), value)
    try:
        body = json.loads(request.body) if request.body else None
    except (ValueError, UnicodeError, RecursionError):
        body = None
    refs = {ref.lower() if ref.startswith("headers.") else ref for ref in secret_refs}
    refs.update("headers." + name for name in headers if name.lower() in SENSITIVE)
    refs.update("query." + name for name in query if name.lower() in SENSITIVE)

    # Arrays have no index-reference syntax. Mark the enclosing array when any
    # descendant contains a sensitive key, so whole-array substitutions stay secret.
    pending = [(body, "body", None)]
    while pending:
        node, prefix, array_root = pending.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                current = prefix + "." + key
                if key.lower() in SENSITIVE:
                    refs.add(array_root or current)
                pending.append((value, current, array_root))
        elif isinstance(node, list):
            pending.extend((child, prefix, array_root or prefix) for child in node)

    return TemplateContext(path, query, headers, body, refs)


def lookup(ref, context):
    namespace, *keys = ref.split(".")
    value = getattr(context, namespace)
    if namespace == "headers":
        keys = [".".join(keys).lower()]
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            raise RouterError(
                400, "TEMPLATE_ERROR", "A required template value is missing or invalid."
            )
        value = value[key]
    return value


def is_secret(ref, context):
    normalized = ref.lower() if ref.startswith("headers.") else ref
    for secret in context.secret_refs:
        candidate = secret.lower() if secret.startswith("headers.") else secret
        if (
            normalized == candidate
            or normalized.startswith(candidate + ".")
            or candidate.startswith(normalized + ".")
        ):
            return True
    return False


def tainted(template, context):
    if isinstance(template, str):
        return any(is_secret(m.group(1), context) for m in REFERENCE.finditer(template))
    if isinstance(template, dict):
        return any(tainted(v, context) for v in template.values())
    if isinstance(template, list):
        return any(tainted(v, context) for v in template)
    return False


def scalar(value):
    if isinstance(value, (dict, list)):
        raise RouterError(
            400, "TEMPLATE_ERROR", "Objects and arrays cannot be interpolated into text."
        )
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render_text(template, context, encode=False):
    def replace(match):
        value = scalar(lookup(match.group(1), context))
        return (
            quote(value, safe="").replace(".", "%2E")
            if encode and value in {".", ".."}
            else quote(value, safe="")
            if encode
            else value
        )

    return REFERENCE.sub(replace, template)


def render_json(template, context):
    def transform(value):
        if isinstance(value, str):
            match = REFERENCE.fullmatch(value)
            return lookup(match.group(1), context) if match else render_text(value, context)
        if isinstance(value, dict):
            return {k: transform(v) for k, v in value.items()}
        if isinstance(value, list):
            return [transform(v) for v in value]
        return value

    return json.dumps(
        transform(template), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode()


def render_request(spec, context, original_body, content_type):
    target = spec.target
    parts = urlsplit(target.url)
    secrets = set()
    path = render_text(parts.path, context, encode=True)
    # HTTPX removes literal dot segments. Check the composed segment, including
    # fixed text and adjacent/empty substitutions, before constructing its URL.
    path = "/".join(
        segment.replace(".", "%2E") if segment in {".", ".."} else segment
        for segment in path.split("/")
    )
    query = [(item.name, render_text(item.value or "", context)) for item in target.query]
    extra_query = urlencode(query)
    url = urlunsplit(
        (parts.scheme, parts.netloc, path, "&".join(p for p in [parts.query, extra_query] if p), "")
    )
    if tainted(parts.path, context) or any(
        f.secret or tainted(f.value, context) for f in target.query
    ):
        secrets.add("url")
    headers = []
    for header in target.headers:
        value = render_text(header.value or "", context)
        if "\r" in value or "\n" in value:
            raise RouterError(400, "TEMPLATE_ERROR", "Invalid rendered header value.")
        try:
            value.encode("ascii")
        except UnicodeEncodeError:
            raise RouterError(400, "TEMPLATE_ERROR", "HTTP header values must be ASCII.") from None
        headers.append((header.name, value))
        if header.secret or header.name.lower() in SENSITIVE or tainted(header.value, context):
            secrets.add("headers." + header.name.lower())
    mode = target.body.mode
    body = b""
    if mode == "passthrough":
        body = original_body
        if content_type and not any(k.lower() == "content-type" for k, _ in headers):
            headers.append(("Content-Type", content_type))
    elif mode == "text":
        body = render_text(target.body.template, context).encode()
    elif mode == "json":
        body = render_json(target.body.template, context)
        if not any(k.lower() == "content-type" for k, _ in headers):
            headers.append(("Content-Type", "application/json"))
    if target.body.secret or tainted(target.body.template, context):
        secrets.add("body")
    return RenderedRequest(target.method, url, headers, body, secrets)
