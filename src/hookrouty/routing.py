import re
from dataclasses import dataclass
from urllib.parse import unquote_to_bytes

from .models import RouterError


class RouteConflict(ValueError):
    pass


@dataclass
class CompiledRoute:
    method: str
    pattern: str
    segments: list[str]
    mask: tuple[int, ...]
    hook_id: str


@dataclass
class RouteMatch:
    hook_id: str
    path: dict[str, str]


def compile_route(method, pattern, hook_id=""):
    if (
        not pattern.startswith("/hooks/")
        or any(c in pattern for c in "?#%\\\0")
        or len(pattern) > 2048
    ):
        raise ValueError("Input path must be under /hooks/ without query, escapes or fragment")
    segments = pattern.split("/")
    names = []
    for i, segment in enumerate(segments):
        if not segment and i not in {0, len(segments) - 1}:
            raise ValueError("Empty route segments are not allowed")
        if segment.startswith(":"):
            name = segment[1:]
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or name in names:
                raise ValueError("Invalid or duplicate path parameter")
            names.append(name)
        elif ":" in segment or any(ord(c) < 33 for c in segment):
            raise ValueError("Invalid route segment")
    return CompiledRoute(
        method, pattern, segments, tuple(0 if s.startswith(":") else 1 for s in segments), hook_id
    )


def validate_route_set(routes):
    for i, left in enumerate(routes):
        for right in routes[i + 1 :]:
            if left.method != right.method or left.mask != right.mask:
                continue
            if all(a == b or a.startswith(":") for a, b in zip(left.segments, right.segments)):
                raise RouteConflict("A route with the same priority already exists")


def resolve_route(routes, method, raw_path):
    path = raw_path.split(b"?", 1)[0]
    if (
        re.search(rb"%(?![a-fA-F0-9]{2})", path)
        or re.search(rb"%(?:2f|5c|00)", path, re.I)
        or b"\\" in path
        or b"\0" in path
    ):
        raise RouterError(400, "INVALID_INPUT", "Invalid path encoding.")
    try:
        segments = unquote_to_bytes(path).decode("utf-8", errors="strict").split("/")
    except UnicodeDecodeError:
        raise RouterError(400, "INVALID_INPUT", "Invalid path encoding.") from None
    matched = []
    for route in routes:
        if len(route.segments) != len(segments):
            continue
        if all(
            (value != "" if expected.startswith(":") else expected == value)
            for expected, value in zip(route.segments, segments)
        ):
            matched.append(route)
    candidates = [r for r in matched if r.method == method]
    if not candidates:
        raise RouterError(
            405 if matched else 404,
            "METHOD_NOT_ALLOWED" if matched else "HOOK_NOT_FOUND",
            "Method not allowed." if matched else "Hook not found.",
        )
    route = max(candidates, key=lambda r: r.mask)
    return RouteMatch(
        route.hook_id,
        {
            expected[1:]: value
            for expected, value in zip(route.segments, segments)
            if expected.startswith(":")
        },
    )
