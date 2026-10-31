"""Linux Docker smoke test; no Cloudflare account/secrets or paid AI calls.

Uses an HTTP test double for the private R2 bridge, including ETag fencing.
Kills the container after acknowledged writes and restores into a fresh disk.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Storage(BaseHTTPRequestHandler):
    raw: bytes | None = None
    etag: str | None = None
    lock = threading.Lock()

    def log_message(self, *_args):
        pass

    def do_GET(self):
        with Storage.lock:
            data = Storage.raw
            self.send_response(200 if data else 404)
            if data:
                self.send_header("ETag", Storage.etag)
            self.end_headers()
            if data:
                self.wfile.write(data)

    def do_PUT(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        with Storage.lock:
            matches = self.headers.get("If-Match") == Storage.etag if Storage.raw else (
                self.headers.get("If-None-Match") == "*"
            )
            if not matches:
                self.send_response(412)
                self.end_headers()
                return
            Storage.raw = raw
            Storage.etag = '"' + hashlib.sha256(raw).hexdigest() + '"'
            self.send_response(204)
            self.send_header("ETag", Storage.etag)
            self.end_headers()


def api(path, body=None, raw=None):
    headers = {"X-User": "smoke-lead", "X-Role": "lead"}
    if body is not None:
        raw = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif raw is not None:
        headers["Content-Type"] = "application/zip"
    request = urllib.request.Request("http://127.0.0.1:8000/api/v1/" + path,
                                     data=raw, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise AssertionError(f"{path}: HTTP {exc.code}: {exc.read().decode()[:2000]}") from exc


def start(image):
    args = ["docker", "run", "--detach", "--network", "host", "--memory", "4g"]
    env = {
        "APP_ENV": "demo", "AI_ENABLED": "false", "LIVE_ENABLED": "false",
        "DEMO_MODE": "true", "DUCKDB_PATH": "/state/megaday.duckdb",
        "LIVE_DIR": "/state/live", "DATA_DIR": "/seed-input",
        "MEGADAY_STATE_ROOT": "/state", "MEGADAY_SEED_DB": "/seed/megaday.duckdb",
        "MEGADAY_STATE_URL": "http://127.0.0.1:9000/state",
    }
    for key, value in env.items():
        args += ["--env", f"{key}={value}"]
    cid = subprocess.check_output([*args, image], text=True).strip()
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            if api("health")["data"]["status"] == "ok":
                return cid
        except (OSError, AssertionError):
            time.sleep(2)
    subprocess.run(["docker", "logs", "--tail", "100", cid], check=False)
    subprocess.run(["docker", "rm", "-f", cid], check=False)
    raise AssertionError("Container did not become healthy")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", 9000), Storage)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cid = None
    try:
        cid = start(args.image)
        context = api("campaign-analysis/CD002")
        assert context["goal"] is None
        preview = api("sale-plans/preview", {
            "campaign_id": "CD002", "expected_goal": None,
            "goal": {"kind": "profit", "deadline": "2026-11-11T04:00:00+07:00"},
            "start": "2026-11-11T00:00:00+07:00", "horizon_hours": 4, "samples": 50,
        })
        api(f"sale-plans/{preview['data']['preview_id']}/apply",
            {"confirmed": True, "expected_goal": None})
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as archive:
            source = Path(__file__).resolve().parents[1] / "mock-data/out/demo/live_11_11/_replay.json"
            archive.write(source, "live_11_11/_replay.json")
        api("live/replay-archive", raw=raw.getvalue())
        api("live/control", {"command": "jump", "hour": 0, "wait": True})
        before = api("campaign-analysis/CD002")
        assert before["dataAsOf"] == "2026-11-11T01:00:00+07:00"
        result = api("campaign-analysis/CD002/refresh", {
            "revision": before["revision"], "goal_id": before["goal"]["goal_id"],
        })
        scenario = result["scenario"]
        assert Storage.raw and len(Storage.raw) < 96 * 1024 * 1024
        print(f"Snapshot bytes: {len(Storage.raw)}", flush=True)
        # SIGKILL: proves acknowledged changes survive without graceful shutdown.
        subprocess.run(["docker", "kill", cid], check=True)
        subprocess.run(["docker", "rm", cid], check=True)
        cid = None
        cid = start(args.image)
        restored = api("campaign-analysis/CD002")
        assert restored["goal"] == before["goal"]
        assert restored["dataAsOf"] == before["dataAsOf"]
        assert api("live/replay-archive")["uploaded"]
        assert api("live/status")["data"]["replay_hour"] == 0
        trace = api(f"scenarios/{scenario['scenario_id']}")["data"]["calculation_trace"]
        assert trace == scenario["calculation_trace"]
        api("live/control", {"command": "jump", "hour": 1, "wait": True})
        updated = api("campaign-analysis/CD002")
        assert updated["dataAsOf"] == "2026-11-11T02:00:00+07:00"
        assert updated["revision"] != restored["revision"]
        assert updated["goal"] == restored["goal"]
        print("PASS: goal, uploaded replay, clock and calculation trace survived a fresh container; next hour works.", flush=True)
    finally:
        if cid:
            subprocess.run(["docker", "logs", "--tail", "60", cid], check=False)
            subprocess.run(["docker", "rm", "-f", cid], check=False)
        server.shutdown()


if __name__ == "__main__":
    main()
