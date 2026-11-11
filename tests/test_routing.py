import pytest

from hookrouty.models import HookSpec, IncomingRequest, RouterError
from hookrouty.routing import RouteConflict, compile_route, resolve_route, validate_route_set
from hookrouty.templating import build_context, render_request


def test_route_priority_and_collision():
    parameter = compile_route("POST", "/hooks/:name", "parameter")
    literal = compile_route("POST", "/hooks/new", "literal")
    assert resolve_route([parameter, literal], "POST", b"/hooks/new").hook_id == "literal"
    with pytest.raises(RouteConflict):
        validate_route_set([parameter, compile_route("POST", "/hooks/:other")])
    with pytest.raises(RouterError) as error:
        resolve_route([literal], "GET", b"/hooks/new")
    assert error.value.status_code == 405


@pytest.mark.parametrize(
    "path", [b"/hooks/%2F", b"/hooks/%5c", b"/hooks/%00", b"/hooks/%FF", b"/hooks/%QQ"]
)
def test_invalid_encoded_paths(path):
    with pytest.raises(RouterError) as error:
        resolve_route([compile_route("POST", "/hooks/:name")], "POST", path)
    assert error.value.status_code == 400


def test_single_decode_and_trailing_slash():
    route = compile_route("POST", "/hooks/:name", "x")
    assert resolve_route([route], "POST", b"/hooks/%252F").path == {"name": "%2F"}
    with pytest.raises(RouterError):
        resolve_route([route], "POST", b"/hooks/a/")


def test_json_templates_and_url_encoding():
    spec = HookSpec(
        name="Deploy",
        input_path="/hooks/:project",
        target={
            "url": "http://upstream/apps/{{path.project}}?fixed=1",
            "query": [{"name": "q", "value": "{{query.q}}"}],
            "body": {
                "mode": "json",
                "template": {"enabled": "{{body.enabled}}", "name": "prefix {{body.name}}"},
            },
        },
    )
    incoming = IncomingRequest(
        "id",
        "POST",
        b"/hooks/a",
        [("q", "first"), ("q", "second")],
        [("Content-Type", "application/json")],
        b'{"enabled":true,"name":"a\\"b"}',
    )
    context = build_context(incoming, {"project": "a/b?z"}, set())
    result = render_request(spec, context, incoming.body, "application/json")
    assert result.url == "http://upstream/apps/a%2Fb%3Fz?fixed=1&q=first"
    import json

    assert json.loads(result.body) == {"enabled": True, "name": 'prefix a"b'}


def test_missing_and_object_interpolation():
    from hookrouty.models import TemplateContext
    from hookrouty.templating import render_text

    context = TemplateContext({}, {}, {}, {"object": {}}, set())
    for value in ["{{query.missing}}", "prefix {{body.object}}"]:
        with pytest.raises(RouterError):
            render_text(value, context)
