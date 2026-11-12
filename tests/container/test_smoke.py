import base64
import contextlib
import gzip
import os
import subprocess
import time
from uuid import uuid4

import httpx
import pytest

pytestmark = [
    pytest.mark.container,
    pytest.mark.skipif(
        not os.getenv("HOOKROUTY_IMAGE"), reason="Set HOOKROUTY_IMAGE to a locally built image"
    ),
]


def docker(*args, check=True, input=None):
    result = subprocess.run(
        ["docker", *args], check=check, input=input, text=True, capture_output=True
    )
    if check and result.returncode:
        pytest.fail(result.stderr)
    return (result.stdout + result.stderr if args[0] == "logs" else result.stdout).strip()


def wait_ready(url):
    for _ in range(60):
        with contextlib.suppress(httpx.HTTPError):
            if httpx.get(url + "/healthz", timeout=0.5).status_code == 200:
                return
        time.sleep(0.25)
    pytest.fail("Container did not become healthy")


UPSTREAM = """
from http.server import BaseHTTPRequestHandler, HTTPServer
import gzip, json
class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body=self.rfile.read(int(self.headers.get('Content-Length','0')))
        if self.path.startswith('/gzip'):
            payload=gzip.compress(b'compressed-response')
            self.send_response(200);self.send_header('Content-Encoding','gzip');self.send_header('Content-Type','text/plain');self.end_headers();self.wfile.write(payload)
        else:
            self.send_response(201);self.send_header('Content-Type','application/json');self.send_header('Set-Cookie','bad=1');self.end_headers();self.wfile.write(json.dumps({'path':self.path,'body':body.decode(),'password':'response-secret'}).encode())
    def log_message(self,*args): pass
HTTPServer(('0.0.0.0',8000),Handler).serve_forever()
"""


def test_container_roundtrip_restart_backup_and_recovery(tmp_path):
    image = os.environ["HOOKROUTY_IMAGE"]
    prefix = "hookrouty-test-" + uuid4().hex[:10]
    network, volume, app, upstream = [
        prefix + suffix for suffix in ["-net", "-data", "-app", "-upstream"]
    ]
    restored_volume = prefix + "-restored-data"
    key = base64.urlsafe_b64encode(b"c" * 32).decode()
    try:
        docker("network", "create", network)
        docker("volume", "create", volume)
        docker(
            "run",
            "-d",
            "--name",
            upstream,
            "--network",
            network,
            "--network-alias",
            "upstream",
            "--entrypoint",
            "python",
            image,
            "-u",
            "-c",
            UPSTREAM,
        )
        docker(
            "run",
            "-d",
            "--name",
            app,
            "--network",
            network,
            "-p",
            "127.0.0.1::8080",
            "--read-only",
            "--tmpfs",
            "/tmp",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "-v",
            volume + ":/data",
            "-e",
            "APP_SECRET_KEY=" + key,
            "-e",
            "ADMIN_PASSWORD=initial-password",
            image,
        )
        port = docker("port", app, "8080/tcp").rsplit(":", 1)[1]
        url = "http://127.0.0.1:" + port
        wait_ready(url)
        assert docker("exec", app, "id", "-u") != "0"
        with httpx.Client(base_url=url) as client:

            def login(password="initial-password"):
                csrf = client.get("/api/session").json()["csrf_token"]
                client.headers["X-CSRF-Token"] = csrf
                response = client.post(
                    "/api/login", json={"username": "admin", "password": password}
                )
                assert response.status_code == 200, response.text
                client.headers["X-CSRF-Token"] = response.json()["csrf_token"]

            login()
            user = client.post(
                "/api/users", json={"username": "persisted", "password": "member-password"}
            )
            assert user.status_code == 201
            spec = {
                "name": "Persistent hook",
                "enabled": True,
                "input_path": "/hooks/demo/:project",
                "capture_bodies": True,
                "target": {
                    "url": "http://upstream:8000/{{path.project}}",
                    "body": {"mode": "passthrough"},
                },
            }
            created = client.post("/api/hooks", json=spec).json()
            hid, token = created["id"], created["token"]
            assert client.post("/api/hooks", content=b"x" * 1048577).status_code == 413
            for _ in range(2):
                malformed = client.post(
                    "/hooks/demo/example",
                    headers={"X-Webhook-Token": token, "Content-Type": "application/json"},
                    content=b'{"value":"\\ud800"}',
                )
                assert malformed.status_code == 201
                assert "sandbox" in malformed.headers["content-security-policy"]

            result = client.post(
                "/hooks/demo/example",
                headers={"X-Webhook-Token": token, "Content-Type": "application/json"},
                content=b'{"ok":true}',
            )
            assert result.status_code == 201, result.text
            assert result.json()["path"] == "/example"
            assert "set-cookie" not in result.headers
            rid = result.headers["x-hookrouty-request-id"]
            assert client.get("/api/executions/" + rid).json()["outcome"] == "success"
            assert "response-secret" not in client.get("/api/executions/" + rid).text
            client.patch(
                "/api/hooks/" + hid, json={"target": {"url": "http://upstream:8000/gzip"}}
            ).raise_for_status()
            compressed = client.post("/hooks/demo/a", headers={"X-Webhook-Token": token})
            assert compressed.content == b"compressed-response"
            assert compressed.headers["content-encoding"] == "gzip"
            assert int(compressed.headers["content-length"]) == len(
                gzip.compress(b"compressed-response")
            )
            docker("restart", app)
            port = docker("port", app, "8080/tcp").rsplit(":", 1)[1]
            url = "http://127.0.0.1:" + port
            client.base_url = url
            wait_ready(url)
            login()
            assert client.get("/api/hooks/" + hid).status_code == 200
            assert client.get("/api/executions/" + rid).status_code == 200
            logs = docker("logs", app)
            assert rid in logs
            assert token not in logs and "response-secret" not in logs
            assert any(u["username"] == "persisted" for u in client.get("/api/users").json())
            docker(
                "exec",
                app,
                "python",
                "-m",
                "hookrouty.admin",
                "backup",
                "--output",
                "/data/backup.sqlite3",
            )
            docker(
                "exec",
                "-i",
                app,
                "python",
                "-m",
                "hookrouty.admin",
                "reset-password",
                "--username",
                "admin",
                "--password-stdin",
                input="recovered-password\n",
            )
            assert client.get("/api/users").status_code == 401
            login("recovered-password")
        docker("volume", "create", restored_volume)
        docker(
            "run",
            "--rm",
            "--user",
            "0:0",
            "--entrypoint",
            "python",
            "-v",
            volume + ":/original:ro",
            "-v",
            restored_volume + ":/data",
            image,
            "-c",
            "import os,shutil; shutil.copyfile('/original/backup.sqlite3','/data/hookrouty.sqlite3'); os.chown('/data/hookrouty.sqlite3',10001,10001); os.chown('/data',10001,10001)",
        )
        # Restore backup into a new volume and start without bootstrap credentials; changed bootstrap is ignored.
        docker("rm", "-f", "-v", app)
        docker(
            "run",
            "-d",
            "--name",
            app,
            "-p",
            "127.0.0.1::8080",
            "-v",
            restored_volume + ":/data",
            "-e",
            "APP_SECRET_KEY=" + key,
            image,
        )
        port = docker("port", app, "8080/tcp").rsplit(":", 1)[1]
        restored_url = "http://127.0.0.1:" + port
        wait_ready(restored_url)
        with httpx.Client(base_url=restored_url) as restored:
            csrf = restored.get("/api/session").json()["csrf_token"]
            response = restored.post(
                "/api/login",
                headers={"X-CSRF-Token": csrf},
                json={"username": "admin", "password": "initial-password"},
            )
            assert response.status_code == 200
            assert restored.get("/api/hooks/" + hid).status_code == 200
            assert restored.get("/api/executions/" + rid).status_code == 200
        docker("rm", "-f", "-v", app)
        # Fresh DB must refuse to start without the initial password.
        docker("run", "--name", app, "-e", "APP_SECRET_KEY=" + key, image, check=False)
        assert docker("inspect", "--format", "{{.State.ExitCode}}", app) != "0"
    finally:
        docker("rm", "-f", "-v", app, upstream, check=False)
        docker("volume", "rm", volume, restored_volume, check=False)
        docker("network", "rm", network, check=False)
