import base64
import contextlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("RUN_E2E") != "1", reason="Set RUN_E2E=1 to run Chromium tests"
    ),
]


@pytest.fixture
def server(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        **os.environ,
        "APP_SECRET_KEY": base64.urlsafe_b64encode(b"e" * 32).decode(),
        "ADMIN_PASSWORD": "initial-password",
        "DATA_DIR": str(tmp_path),
        "APP_URL": f"http://127.0.0.1:{port}",
        "PYTHONPATH": str(Path(__file__).parents[2] / "src"),
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "hookrouty.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-access-log",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            with contextlib.suppress(httpx.HTTPError):
                if httpx.get(url + "/healthz").status_code == 200:
                    break
            time.sleep(0.1)
        else:
            pytest.fail("Server did not start")
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_user_journey(server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(server + "/login")
        page.get_by_label("Username", exact=True).fill("admin")
        page.get_by_label("Password", exact=True).fill("initial-password")
        page.get_by_role("button", name="Sign in", exact=True).click()
        page.wait_for_url("**/app/hooks")
        page.get_by_role("link", name="Users", exact=True).click()
        page.get_by_label("Username", exact=True).fill("member")
        page.get_by_label("Initial password", exact=True).fill("member-password")
        page.get_by_role("button", name="Create account", exact=True).click()
        page.get_by_role("cell", name="member", exact=True).wait_for()
        page.get_by_role("button", name="Sign out", exact=True).click()
        page.wait_for_url("**/login")
        page.get_by_label("Username", exact=True).fill("member")
        page.get_by_label("Password", exact=True).fill("member-password")
        page.get_by_role("button", name="Sign in", exact=True).click()
        page.wait_for_url("**/app/hooks")
        page.get_by_role("link", name="+ Create hook", exact=True).click()
        page.get_by_role("link", name="Advanced editor", exact=True).click()
        page.get_by_label("Name", exact=True).fill("<script>window.xss=1</script>")
        page.get_by_label("Route", exact=True).fill("/hooks/demo/:project")
        page.get_by_label("HTTP(S) URL", exact=True).fill("http://127.0.0.1:1/{{path.project}}")
        page.get_by_role("button", name="+ Add header", exact=True).click()
        page.get_by_label("headers field name").fill("Authorization")
        page.get_by_label("Value / template", exact=True).fill("Bearer private-value")
        page.get_by_label("Secret", exact=True).check()
        page.get_by_role("button", name="Save hook", exact=True).click()
        page.get_by_role("status").filter(has_text="Store this token").wait_for()
        page.get_by_role("link", name="Hooks", exact=True).click()
        page.get_by_role("link", name="Test", exact=True).click()
        page.get_by_role("button", name="Preview only", exact=True).click()
        page.locator("#test-result").filter(has_text="REDACTED").wait_for()
        assert "private-value" not in page.locator("#test-result").inner_text()
        assert page.evaluate("window.xss") is None
        page.on("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button", name="Send real test", exact=True).click()
        page.get_by_role("status").filter(has_text="Destination connection failed").wait_for()
        page.get_by_role("link", name="Activity", exact=True).click()
        page.locator("#executions-list tr").first.wait_for()
        assert "manual_test" in page.locator("#executions-list").inner_text()
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.get_by_role("link", name="Hooks", exact=True).click()
        page.get_by_role("link", name="Edit", exact=True).click()
        assert page.get_by_label("Name", exact=True).input_value().startswith("<script>")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        Path("test-results").mkdir(exist_ok=True)
        page.screenshot(path="test-results/editor-mobile.png", full_page=True)
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.goto(server + "/app/hooks")
        page.locator("#hooks-list tr").first.wait_for()
        page.screenshot(path="test-results/hooks-desktop.png", full_page=True)
        assert not errors
        browser.close()


def test_public_document_is_script_free_and_has_opaque_origin(server):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from playwright.sync_api import sync_playwright

    class Document(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Security-Policy", "script-src 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(
                b"<!doctype html><h1>Upstream document</h1><script>window.documentScriptRan=true</script>"
            )

        def log_message(self, *args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Document)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context()
            session = context.request.get(server + "/api/session").json()
            signed = context.request.post(
                server + "/api/login",
                headers={"X-CSRF-Token": session["csrf_token"]},
                data={"username": "admin", "password": "initial-password"},
            ).json()
            created = context.request.post(
                server + "/api/hooks",
                headers={"X-CSRF-Token": signed["csrf_token"]},
                data={
                    "name": "Document",
                    "enabled": True,
                    "input_method": "GET",
                    "input_path": "/hooks/document",
                    "auth": {"mode": "query"},
                    "target": {
                        "method": "GET",
                        "url": "http://127.0.0.1:" + str(upstream.server_port),
                    },
                },
            ).json()
            page = context.new_page()
            page.goto(server + "/hooks/document?token=" + created["token"])
            assert page.get_by_role("heading", name="Upstream document").is_visible()
            assert page.evaluate("window.documentScriptRan") is None
            assert page.evaluate("window.origin") == "null"
            assert (
                page.evaluate(
                    "() => {try { return window.localStorage.length; } catch(error) { return error.name; }}"
                )
                == "SecurityError"
            )
            page.goto(server + "/app/hooks")
            assert page.get_by_role("heading", name="Your hooks").is_visible()
            browser.close()
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)


def wizard_login(context, server):
    session = context.request.get(server + "/api/session").json()
    signed = context.request.post(
        server + "/api/login",
        headers={"X-CSRF-Token": session["csrf_token"]},
        data={"username": "admin", "password": "initial-password"},
    )
    assert signed.ok
    return signed.json()["csrf_token"]


def test_simple_wizard_copy_and_real_call(server):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from playwright.sync_api import sync_playwright

    calls = []

    class Destination(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(dict(self.headers))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Destination)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(
                viewport={"width": 390, "height": 844},
                permissions=["clipboard-read", "clipboard-write"],
            )
            wizard_login(context, server)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(server + "/app/hooks/new")
            page.get_by_label("Webhook name", exact=True).fill("Simple call")
            page.get_by_label("Template", exact=True).select_option("simple-http-url")
            page.get_by_role("button", name="Continue", exact=True).click()
            page.get_by_label("Destination URL", exact=True).fill(
                "http://127.0.0.1:" + str(upstream.server_port)
            )
            page.get_by_label("Bearer token (optional)", exact=True).fill("destination-private")
            page.get_by_role("button", name="Continue", exact=True).click()
            page.locator("#wizard-destination").filter(has_text="POST").wait_for()
            assert "destination-private" not in page.locator("#wizard-preview").text_content()
            assert not calls
            page.get_by_role("button", name="Back", exact=True).click()
            assert (
                page.get_by_label("Bearer token (optional)").input_value() == "destination-private"
            )
            page.get_by_role("button", name="Continue", exact=True).click()
            page.get_by_label("Caller authentication").select_option("query")
            page.get_by_role("button", name="Create webhook", exact=True).click()
            page.locator("#wizard-result").wait_for(state="visible")
            url = page.get_by_label("Webhook URL", exact=True).input_value()
            token = page.get_by_label("Webhook token", exact=True).input_value()
            assert url.startswith(server + "/hooks/simple-call-") and "?token=" in url
            assert not calls
            page.get_by_role("button", name="Copy URL", exact=True).click()
            page.wait_for_function(
                "navigator.clipboard.readText().then(value => value.includes('?token='))"
            )
            assert page.evaluate("navigator.clipboard.readText()") == url
            page.evaluate(
                "window.savedClipboard = navigator.clipboard; Object.defineProperty(navigator, 'clipboard', {value: undefined, configurable: true})"
            )
            page.get_by_role("button", name="Copy token", exact=True).click()
            page.get_by_role("status").filter(has_text="Copied.").wait_for()
            assert page.evaluate("window.savedClipboard.readText()") == token
            assert page.get_by_label("Webhook token").get_attribute("type") == "password"
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            assert context.request.post(url).status == 204
            assert len(calls) == 1 and calls[0]["Authorization"] == "Bearer destination-private"
            assert "X-Webhook-Token" not in calls[0]
            Path("test-results").mkdir(exist_ok=True)
            page.screenshot(path="test-results/wizard-mobile.png", full_page=True)
            assert not errors
            browser.close()
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)


def test_dynamic_template_and_dockhand_wizard(server):
    import json

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        wizard_login(context, server)
        page = context.new_page()
        page.goto(server + "/app/templates")
        page.get_by_label("Name", exact=True).fill("My deployment")
        page.get_by_label("Input fields (JSON array)").fill(
            json.dumps(
                [
                    {"key": "url", "label": "Custom URL", "kind": "url"},
                    {"key": "credential", "label": "Custom token", "kind": "secret"},
                ]
            )
        )
        page.get_by_label("Destination mapping (JSON object)").fill(
            json.dumps(
                {
                    "method": "PUT",
                    "url": "{{inputs.url}}",
                    "headers": [{"name": "X-Custom", "value": "{{inputs.credential}}"}],
                }
            )
        )
        page.get_by_role("button", name="Save template", exact=True).click()
        page.get_by_role("status").filter(has_text="Template saved").wait_for()
        page.goto(server + "/app/hooks/new")
        page.get_by_label("Webhook name").fill("Custom call")
        page.get_by_label("Template", exact=True).select_option(label="My deployment")
        page.get_by_role("button", name="Continue", exact=True).click()
        page.get_by_label("Custom URL").fill("http://destination:8000/run")
        page.get_by_label("Custom token").fill("custom-private")
        page.get_by_role("button", name="Continue", exact=True).click()
        page.locator("#wizard-destination").filter(has_text="PUT").wait_for()
        assert "custom-private" not in page.locator("#wizard-preview").text_content()
        page.get_by_role("button", name="Create webhook", exact=True).click()
        page.locator("#wizard-result").wait_for(state="visible")
        assert "X-Webhook-Token:" in page.get_by_label("Ready-to-use curl").input_value()
        page.goto(server + "/app/hooks/new")
        page.get_by_label("Webhook name").fill("Deploy stack")
        page.get_by_label("Template", exact=True).select_option("dockhand-redeploy")
        page.get_by_role("button", name="Continue", exact=True).click()
        page.get_by_label("Dockhand URL", exact=True).fill("http://dockhand:3000")
        page.get_by_label("Stack name").fill("myapp")
        page.get_by_label("Environment ID (optional)").fill("2")
        page.get_by_label("Dockhand API token").fill("dh_private")
        page.get_by_role("button", name="Continue", exact=True).click()
        page.locator("#wizard-destination").filter(
            has_text="/api/stacks/myapp/deploy?env=2"
        ).wait_for()
        preview = page.locator("#wizard-preview").text_content()
        assert "dh_private" not in preview and "forceRecreate" in preview
        Path("test-results").mkdir(exist_ok=True)
        page.screenshot(path="test-results/wizard-desktop.png", full_page=True)
        page.get_by_role("button", name="Create webhook", exact=True).click()
        page.locator("#wizard-result").wait_for(state="visible")
        hooks = context.request.get(server + "/api/hooks").json()
        assert len(hooks) == 2
        hook = next(hook for hook in hooks if hook["name"] == "Deploy stack")
        assert hook["timeout_seconds"] == 300 and hook["target"]["headers"][0]["secret"]
        assert context.request.get(server + "/api/executions").json()["total"] == 0
        browser.close()


def test_saved_hook_curl_page_roundtrip_and_rotation(server):
    import json
    import shlex
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from playwright.sync_api import sync_playwright

    calls = []

    class Destination(BaseHTTPRequestHandler):
        def do_PATCH(self):
            calls.append((self.path, self.rfile.read(int(self.headers.get("Content-Length", "0")))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"called")

        def log_message(self, *args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Destination)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(
                viewport={"width": 390, "height": 844},
                permissions=["clipboard-read", "clipboard-write"],
            )
            csrf = wizard_login(context, server)
            headers = {"X-CSRF-Token": csrf}
            secret = "token'&$()private"
            created = context.request.post(
                server + "/api/hooks",
                headers=headers,
                data={
                    "name": "Saved command",
                    "enabled": True,
                    "input_method": "PATCH",
                    "input_path": "/hooks/use/:project",
                    "auth": {"mode": "query", "token": secret},
                    "target": {
                        "method": "PATCH",
                        "url": "http://127.0.0.1:"
                        + str(upstream.server_port)
                        + "/{{path.project}}",
                        "body": {"mode": "passthrough"},
                    },
                },
            ).json()
            hid = created["id"]
            page = context.new_page()
            page.goto(server + "/app/hooks/" + hid + "/edit")
            page.get_by_role("link", name="cURL", exact=True).click()
            page.wait_for_function(
                "document.querySelector('#curl-command').value.includes('REPLACE_WITH_WEBHOOK_TOKEN')"
            )
            assert (
                "REPLACE_WITH_WEBHOOK_TOKEN"
                in page.get_by_label("curl command", exact=True).input_value()
            )
            page.get_by_label("Path parameter: project", exact=True).fill("O'Reilly & cafe")
            page.get_by_label("Webhook token", exact=True).fill(secret)
            body = json.dumps({"message": "O'Reilly $() and new\nline"})
            page.get_by_label("Request body (optional JSON)", exact=True).fill(body)
            command = page.get_by_label("curl command", exact=True).input_value()
            assert "--request PATCH" in command and "--data-binary" in command
            assert not calls
            assert context.request.get(server + "/api/executions").json()["total"] == 0
            page.get_by_role("button", name="Copy curl", exact=True).click()
            page.get_by_role("status").filter(has_text="Copied").wait_for()
            assert page.evaluate("navigator.clipboard.readText()") == command
            result = subprocess.run(
                shlex.split(command.replace("\\\n", "")), capture_output=True, text=True, timeout=10
            )
            assert result.returncode == 0, result.stderr
            assert result.stdout == "called" and len(calls) == 1
            assert (
                calls[0][0] == "/O'Reilly%20%26%20cafe" or calls[0][0] == "/O%27Reilly%20%26%20cafe"
            )
            assert json.loads(calls[0][1]) == json.loads(body)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            assert page.evaluate("localStorage.length + sessionStorage.length") == 0
            page.reload()
            page.wait_for_function(
                "document.querySelector('#curl-command').value.includes('REPLACE_WITH_WEBHOOK_TOKEN')"
            )
            assert page.get_by_label("Webhook token", exact=True).input_value() == ""
            assert secret not in page.get_by_label("curl command", exact=True).input_value()
            page.get_by_text("Lost your webhook token?", exact=True).click()
            # Cancelled rotation must keep the existing credential valid.
            page.once("dialog", lambda dialog: dialog.dismiss())
            page.get_by_role("button", name="Generate new token", exact=True).click()
            assert (
                context.request.patch(
                    server + "/hooks/use/test?token=" + quote(secret, safe="")
                ).status
                == 200
            )
            page.once("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="Generate new token", exact=True).click()
            page.get_by_role("status").filter(has_text="Previous token no longer works").wait_for()
            replacement = page.get_by_label("Webhook token", exact=True).input_value()
            assert replacement and replacement != secret
            assert (
                context.request.patch(
                    server + "/hooks/use/test?token=" + quote(secret, safe="")
                ).status
                == 401
            )
            # Header auth and HEAD use the current saved configuration after navigation.
            assert context.request.patch(
                server + "/api/hooks/" + hid,
                headers=headers,
                data={"input_method": "HEAD", "auth": {"mode": "header"}},
            ).ok
            page.reload()
            page.wait_for_function(
                "document.querySelector('#curl-command').value.includes('--head')"
            )
            page.get_by_label("Webhook token", exact=True).fill(replacement)
            head = page.get_by_label("curl command", exact=True).input_value()
            assert "--head" in head and "--data-binary" not in head
            assert "X-Webhook-Token: " + replacement in head and "?token=" not in head
            assert not page.get_by_label("Request body (optional JSON)").is_visible()
            page.evaluate(
                "window.savedClipboard = navigator.clipboard; Object.defineProperty(navigator, 'clipboard', {value: undefined, configurable: true})"
            )
            page.get_by_role("button", name="Copy curl", exact=True).click()
            page.get_by_role("status").filter(has_text="Copied").wait_for()
            assert page.evaluate("window.savedClipboard.readText()") == head
            page.get_by_label("Path parameter: project", exact=True).fill("invalid/segment")
            assert page.get_by_role("button", name="Copy curl", exact=True).is_disabled()
            page.get_by_label("Path parameter: project", exact=True).fill("example")
            Path("test-results").mkdir(exist_ok=True)
            page.screenshot(path="test-results/curl-mobile.png", full_page=True)
            page.goto(server + "/app/hooks")
            page.get_by_role("link", name="cURL", exact=True).wait_for()
            browser.close()
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)
