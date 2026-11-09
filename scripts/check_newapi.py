"""Import the catalog into a disposable New API container, never production."""

import argparse
import http.cookiejar
import http.server
import json
import secrets
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def check(root, image):
    network = json.loads(command("docker", "network", "inspect", "bridge"))[0]
    gateway = network["IPAM"]["Config"][0]["Gateway"]
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        application_port = port_socket.getsockname()[1]
    files = {}
    for path in (root / "api").rglob("*.json"):
        files["/" + path.relative_to(root).as_posix()] = path.read_bytes()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = files.get(urllib.parse.urlsplit(self.path).path)
            if body is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    fixture = http.server.ThreadingHTTPServer((gateway, 0), Handler)
    fixture_url = f"http://{gateway}:{fixture.server_port}"
    threading.Thread(target=fixture.serve_forever, daemon=True).start()
    name = "newapi-catalog-check-" + secrets.token_hex(4)
    password = secrets.token_urlsafe(28)
    username = "catalogcheck"
    base = f"http://127.0.0.1:{application_port}"
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    headers = {"Content-Type": "application/json"}

    def api(path, data=None, method=None):
        body = None if data is None else json.dumps(data).encode()
        request = urllib.request.Request(base + path, data=body, method=method, headers=headers)
        try:
            with opener.open(request, timeout=25) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"{path}: HTTP {error.code}: {error.read().decode()[:1200]}") from None
        if result.get("success") is not True:
            raise RuntimeError(f"{path}: {result.get('message', result)}")
        return result.get("data")

    started = False
    try:
        command(
            "docker", "run", "--detach", "--name", name,
            "--network", "bridge", "--publish", f"127.0.0.1:{application_port}:3000",
            "--tmpfs", "/data", "--env", "SQLITE_PATH=/data/catalog-test.db",
            "--env", "SESSION_SECRET=" + secrets.token_hex(32),
            "--env", "CRYPTO_SECRET=" + secrets.token_hex(32),
            "--env", "SYNC_UPSTREAM_BASE=" + fixture_url,
            "--env", "TZ=Asia/Shanghai", image, "--log-dir", "/data/logs",
        )
        started = True
        setup = None
        for attempt in range(80):
            try:
                setup = api("/api/setup")
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.25)
        if setup is None or setup.get("status") or setup.get("root_init"):
            raise RuntimeError("test database is not an empty, uninitialized database")
        api("/api/setup", {
            "username": username, "password": password, "confirmPassword": password,
            "SelfUseModeEnabled": False, "DemoSiteEnabled": False,
        })
        login = api("/api/user/login", {"username": username, "password": password})
        headers["New-Api-User"] = str(login["user"]["id"])
        headers["Authorization"] = "Bearer " + login["access_token"]

        metadata = json.loads((root / "api/newapi/models.json").read_text(encoding="utf-8"))
        names = {model["model_name"] for model in metadata["data"]}
        preview = api("/api/models/sync_upstream/preview?locale=zh")
        candidates = preview["candidates"]
        if {item["model_name"] for item in candidates} != names:
            raise AssertionError("metadata preview lost or added model names")
        selections = [{
            "model_name": item["model_name"], "record_version": item["record_version"],
            "create": True, "fields": [field["field"] for field in item["fields"]],
        } for item in candidates]
        api("/api/models/sync_upstream", {
            "locale": "zh", "source_version": preview["source"]["version"],
            "selections": selections,
        })
        imported_preview = api("/api/models/sync_upstream/preview?locale=zh")
        unchanged = [item for item in imported_preview["candidates"] if item["kind"] == "unchanged"]
        if len(unchanged) != len(names):
            raise AssertionError("metadata changed during round-trip import")

        fetched = api("/api/ratio_sync/fetch", {
            "upstreams": [{"id": 900001, "name": "Krill catalog fixture", "base_url": fixture_url,
                           "endpoint": "/api/newapi/ratio_config.json"}],
            "include_unchanged": True, "timeout": 15,
        })
        query = urllib.parse.urlencode([("model", name) for name in sorted(names)])
        snapshot = api("/api/option/model_pricing?" + query)
        pricing = json.loads((root / "api/newapi/ratio_config.json").read_text(encoding="utf-8"))["data"]
        if any(result["status"] != "success" for result in fetched["test_results"]):
            raise AssertionError("native upstream fetch reported an error")
        if set(fetched["prices"]) != names:
            raise AssertionError("native price synchronization lost or added model names")
        versions = {entry["model_name"]: entry["version"] for entry in snapshot["entries"]}
        expected = {}
        changes = []
        for model in sorted(names):
            mode = pricing["billing_mode"][model]
            values = {"billing_setting.billing_mode": mode}
            if mode == "tiered_expr":
                values["billing_setting.billing_expr"] = pricing["billing_expr"][model]
            else:
                values["ModelPrice"] = pricing["model_price"][model]
            expected[model] = values
            upstream_values = next(iter(fetched["prices"][model]["upstreams"].values()))
            sync_values = {
                "billing_setting.billing_mode" if key == "billing_mode" else
                "billing_setting.billing_expr" if key == "billing_expr" else
                "ModelPrice" if key == "model_price" else key: value
                for key, value in upstream_values.items()
            }
            if sync_values != values:
                raise AssertionError("native upstream fetch changed pricing: " + model)
            changes.append({"model_name": model, "expected_version": versions[model], "pricing": values})
        api("/api/option/model_pricing", {"changes": changes}, method="PATCH")
        saved = api("/api/option/model_pricing?" + query)
        for entry in saved["entries"]:
            if entry["configured"] != expected[entry["model_name"]]:
                raise AssertionError("pricing changed during round-trip import: " + entry["model_name"])
        report = {
            "success": True, "image": image,
            "metadata_imported_and_unchanged": len(unchanged),
            "pricing_entries_imported_and_roundtripped": len(saved["entries"]),
            "expressions_validated_by_newapi": len(pricing["billing_expr"]),
            "duration_unit_prices_roundtripped": len(pricing["model_price"]),
            "native_upstream_fetch_response_keys": sorted(fetched) if isinstance(fetched, dict) else [],
            "native_upstream_fetch_values_equal": True,
            "metadata_model_names_exact": True,
            "production_database_accessed": False, "paid_model_calls": 0,
            "scope": "Native JSON parsing, metadata import, expression validation, pricing import and readback; no upstream billing settlement test",
            "remaining_limits": json.loads((root / "manifest.json").read_text(encoding="utf-8"))["limits"],
        }
        return report
    finally:
        if started:
            command("docker", "rm", "--force", name)
        fixture.shutdown()
        fixture.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps(check(args.root, args.image), ensure_ascii=False))
