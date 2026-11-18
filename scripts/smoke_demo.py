"""Exercise checks and booking in a new, isolated local demo; never use a live lab."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend", type=Path, required=True)
    parser.add_argument("--expect-no-ai", action="store_true")
    args = parser.parse_args()
    assert (args.frontend / "index.html").is_file(), "Build the frontend first"
    if args.expect_no_ai:
        assert importlib.util.find_spec("anthropic") is None, "Base install unexpectedly requires the optional AI SDK"
    from labkeeper.demo import CONSOLES

    sockets = []
    try:
        for console_port in CONSOLES:
            probe = socket.socket()
            probe.bind(("127.0.0.1", console_port))
            sockets.append(probe)
    finally:
        for probe in sockets:
            probe.close()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"

    def request(route, method="GET", payload=None, actor="demo-smoke"):
        body = json.dumps(payload).encode() if payload is not None else None
        req = Request(base + route, data=body, method=method, headers={"Content-Type": "application/json", "X-Labkeeper-User": actor})
        with urlopen(req, timeout=12) as response:
            return json.load(response)

    with tempfile.TemporaryDirectory(prefix="labkeeper-public-demo-") as temporary:
        inherited = {"PATH", "HOME", "TMPDIR", "SYSTEMROOT", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR"}
        env = {key: value for key, value in os.environ.items() if key in inherited}
        env["LABKEEPER_FRONTEND_DIST"] = str(args.frontend.resolve())
        log_path = Path(temporary) / "server.log"
        with log_path.open("w") as log:
            process = subprocess.Popen([sys.executable, "-m", "labkeeper", "demo", "--host", "127.0.0.1", "--port", str(port), "--data-dir", temporary], cwd=temporary, env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 40
                while True:
                    try:
                        system = request("/api/v1/system")
                        break
                    except (URLError, OSError):
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise AssertionError("Demo failed to start: " + log_path.read_text()[-4000:]) from None
                        time.sleep(0.25)
                assert system["demo"] is True
                assert system["assistant"]["available"] is False
                assert not any(p["name"] == "meraki" and p["enabled"] for p in system["providers"])
                assert system["last_import"]["invalid"] == 0
                with urlopen(base + "/", timeout=8) as response:
                    assert 'id="root"' in response.read().decode()
                beds = request("/api/v1/testbeds")
                details = [request(f"/api/v1/testbeds/{bed['id']}") for bed in beds if not bed["maintenance"]]
                member_ids = lambda bed: {d["id"] for d in bed["devices"] if not d["is_shared"] and not d["is_virtual"]}
                pair = next((a, b) for a in details for b in details if a["id"] != b["id"] and member_ids(a) & member_ids(b))
                starts = datetime.now(timezone.utc) + timedelta(days=2)
                payload = {"starts_at": starts.isoformat(), "ends_at": (starts + timedelta(hours=1)).isoformat(), "purpose": "Isolated public demo smoke test"}
                booking = request(f"/api/v1/testbeds/{pair[0]['id']}/bookings", "POST", payload)
                try:
                    request(f"/api/v1/testbeds/{pair[1]['id']}/bookings", "POST", payload, actor="demo-smoke-other")
                except HTTPError as error:
                    assert error.code == 409
                else:
                    raise AssertionError("Shared-device conflict was not rejected")
                cancelled = request(f"/api/v1/bookings/{booking['id']}/release", "POST", {})
                assert cancelled["state"] == "cancelled"
                devices = request("/api/v1/devices")
                target = next(device for device in devices if device["name"] == "ALPHA_01")
                result = request(f"/api/v1/devices/{target['id']}/console-test", "POST", {})
                assert any(check["state"] == "ready" for check in result["results"]), result
                events = request("/api/v1/events")
                actions = {event["action"] for event in events if event["actor"] == "demo-smoke"}
                assert {"booked", "cancelled"} <= actions
                print(json.dumps({"demo": True, "testbeds": len(beds), "devices": len(devices), "merakiRequired": False, "assistantRequired": False, "uiServed": True, "consoleReady": True, "sharedBookingConflict": True, "cancellationAndAudit": True}))
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
