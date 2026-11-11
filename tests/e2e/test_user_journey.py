import base64
import contextlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

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
