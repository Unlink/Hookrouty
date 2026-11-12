import json
from urllib.parse import parse_qsl, quote, quote_plus, urlencode, urlsplit, urlunsplit

from .templating import SENSITIVE

REDACTED = "[REDACTED]"


def redact_json(value, refs=(), prefix="body"):
    if any(prefix == r or prefix.startswith(r + ".") for r in refs):
        return REDACTED
    if isinstance(value, dict):
        return {
            key: REDACTED
            if key.lower() in SENSITIVE
            else redact_json(child, refs, prefix + "." + key)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [redact_json(child, refs, prefix) for child in value]
    return value


def safe_url(url, refs=()):
    parts = urlsplit(url)
    query = [
        (key, REDACTED if key.lower() in SENSITIVE or "query." + key in refs else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def safe_headers(headers, secret_fields=()):
    return {
        name: REDACTED
        if name.lower() in SENSITIVE or "headers." + name.lower() in secret_fields
        else value
        for name, value in headers
    }


def capture(
    body, content_type, enabled, refs=(), secret=False, limit=16384, encoded=False, known_values=()
):
    if not enabled or secret:
        return {"omitted": "secret" if secret else "disabled"}
    if encoded:
        return {"omitted": "encoded_body_omitted"}
    media = (content_type or "").lower().split(";", 1)[0]
    if not (media.startswith("text/") or "json" in media):
        return {"omitted": "binary"}
    try:
        text = body.decode("utf-8")
        if "json" in media:
            text = json.dumps(redact_json(json.loads(text), refs), ensure_ascii=False)
        elif refs:
            # Structured secrets cannot safely be removed from arbitrary text.
            return {"omitted": "sensitive_text"}
        text = scrub_known(text, known_values)
        data = text.encode()
    except (UnicodeError, ValueError, RecursionError):
        return {"omitted": "invalid_text"}
    return {"text": data[:limit].decode("utf-8", errors="ignore"), "truncated": len(data) > limit}


def rendered_snapshot(rendered, spec, limit=16384, preview=False, known_values=()):
    headers = dict((k.lower(), v) for k, v in rendered.headers)
    return {
        "method": rendered.method,
        "url": REDACTED
        if "url" in rendered.secret_fields
        else safe_url(rendered.url, spec.secret_refs),
        "headers": safe_headers(rendered.headers, rendered.secret_fields),
        "body": capture(
            rendered.body,
            headers.get("content-type", "text/plain"),
            preview or spec.capture_bodies,
            spec.secret_refs,
            "body" in rendered.secret_fields,
            limit,
            known_values=known_values,
        ),
        "secret_fields": sorted(rendered.secret_fields),
    }


def scrub_known(value, known_values):
    if isinstance(value, str):
        for secret in sorted(set(known_values), key=len, reverse=True):
            if secret:
                value = value.replace(secret, REDACTED)
                try:
                    variants = {quote(secret, safe=""), quote_plus(secret, safe="")}
                except UnicodeError:
                    variants = set()
                for encoded in sorted(variants, key=len, reverse=True):
                    value = value.replace(encoded, REDACTED)
                escaped = json.dumps(secret, ensure_ascii=False)[1:-1]
                if escaped != secret:
                    value = value.replace(escaped, REDACTED)
        return value
    if isinstance(value, dict):
        return {k: scrub_known(v, known_values) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_known(v, known_values) for v in value]
    return value


def known_secrets(context, rendered, spec=None):
    from .models import RouterError
    from .templating import lookup, render_text

    values = set()

    def collect(value):
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, str) and item:
                values.add(item)
                if item.lower().startswith(("bearer ", "basic ")):
                    values.add(item.split(" ", 1)[1])
            elif isinstance(item, (bool, int, float)):
                values.add(json.dumps(item))
            elif isinstance(item, dict):
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)

    for ref in context.secret_refs:
        try:
            collect(lookup(ref, context))
        except RouterError:
            pass
    for name, value in rendered.headers:
        if "headers." + name.lower() in rendered.secret_fields:
            collect(value)
    if spec is not None:
        for item in spec.target.query:
            if item.secret:
                collect(render_text(item.value or "", context))
    if "body" in rendered.secret_fields:
        try:
            collect(json.loads(rendered.body))
        except (ValueError, UnicodeError, RecursionError):
            collect(rendered.body.decode("utf-8", errors="ignore"))
    return values
