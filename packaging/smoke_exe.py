"""Verify the folder executable against copied samples and a fresh private home."""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from photo_explorer import __version__


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="store_true", help="Preflight the same contract against the source CLI")
    args = parser.parse_args()
    scratch = ROOT / ".work"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="exe-smoke-", dir=scratch) as temporary:
        work = Path(temporary)
        home, samples = work / "home", work / "samples"
        shutil.copytree(ROOT / ".work/samples", samples)
        command = ([str(ROOT / ".venv/Scripts/python.exe"), "-m", "photo_explorer"] if args.source else
                   [str(ROOT / "dist/PhotoExplorer/PhotoExplorer.exe")])
        command += ["--no-browser", "--no-tray", "--port", "8766", "--home", str(home)]
        environment = {**os.environ, "PHOTO_EXPLORER_HOME": str(ROOT / ".work/home"), "PYTHONIOENCODING": "utf-8"}
        if not args.source:
            environment.pop("PYTHONPATH", None)
            environment.pop("PYTHONHOME", None)
            environment["PATH"] = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")
        print("Mode: " + ("source preflight" if args.source else "frozen executable"), flush=True)
        print("Launch: " + subprocess.list2cmdline(command), flush=True)
        output_path = work / "process.log"
        with output_path.open("w", encoding="utf-8") as output:
            process = subprocess.Popen(command, cwd=ROOT if args.source else work, env=environment,
                                       stdout=output, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = "http://127.0.0.1:8766"
            def request(path, body=None):
                req = urllib.request.Request(base + path, headers={"Content-Type": "application/json"},
                                             data=json.dumps(body).encode() if body is not None else None)
                with opener.open(req, timeout=15) as response:
                    payload = response.read()
                    return json.loads(payload) if response.headers.get_content_type() == "application/json" else payload
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("Child exited before readiness: " + output_path.read_text(encoding="utf-8"))
                    try:
                        health = request("/api/health")
                        break
                    except (OSError, urllib.error.URLError):
                        time.sleep(.1)
                else:
                    raise RuntimeError("Health check did not become ready in 30 seconds")
                assert health["home"] == str(home) and health["version"] == __version__, health
                assert request("/").startswith(b"<!doctype html>")
                assert b"heartbeat" in request("/static/js/lifecycle.js")
                print(f"Health + bundled UI: PASS version={health['version']}", flush=True)
                root_id = request("/api/roots", {"path": str(samples)})["root"]["id"]
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    scan = request(f"/api/roots/{root_id}/scan")
                    if not scan["running"]:
                        break
                    time.sleep(.1)
                assert not scan["running"] and scan["error"] is None, scan
                print(f"Scan: PASS seen={scan['seen']} metadata={scan['meta_done']}/{scan['meta_total']}", flush=True)
                items = request(f"/api/roots/{root_id}/images?limit=500")["items"]
                jpeg = next(row for row in items if row["name"] == "img1.jpg")
                heic = next(row for row in items if row["name"] == "phone.heic")
                for row, route in ((jpeg, "thumb"), (heic, "thumb"), (heic, "preview")):
                    assert row["meta_state"] == 1, row
                    payload = request(f"/api/images/{row['id']}/{route}")
                    with Image.open(io.BytesIO(payload)) as image:
                        image.load()
                        assert image.format == "JPEG"
                        assert abs(image.width / image.height - row["width"] / row["height"]) < .02
                        # Sample cards have a red encoded-top stripe; square error cards do not.
                        red, green, blue = image.getpixel((image.width // 2, 3))
                        assert red > 180 and green < 100 and blue < 100
                        print(f"{row['name']} {route}: PASS {image.width}x{image.height}, {len(payload)} bytes", flush=True)
                diagnostics = request("/api/diagnostics")
                assert diagnostics["frozen"] is (not args.source)
                assert diagnostics["port"] == 8766 and diagnostics["log_tail"]
                assert any("Startup version=" in line for line in diagnostics["log_tail"])
                print(f"Diagnostics: PASS frozen={diagnostics['frozen']} log_tail={len(diagnostics['log_tail'])} lines", flush=True)
                structure = request("/api/fs/structure?" + urllib.parse.urlencode({"path": str(samples)}))
                assert b"phone.heic" in structure and b"Structure for:" in structure
                rule = {"name": "Smoke HEIC", "scope": {"root_id": root_id},
                        "conditions": [{"field": "ext", "op": "in", "value": ["heic"]}],
                        "action": {"type": "copy", "dest": str(work / "preview-only")}}
                preview = request("/api/rules/preview", {"rule": rule})
                assert preview["total"] == 1 and not (work / "preview-only").exists()
                print(f"Structure export + rule preview: PASS ({len(structure)} bytes, {preview['total']} match)", flush=True)
                # A second headless launch must forward, print the existing URL, and exit.
                second = subprocess.run(command, cwd=ROOT if args.source else work, env=environment,
                                        capture_output=True, timeout=20, check=True,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                assert base.encode() in second.stdout, second.stdout
                print("Second headless launch: PASS existing URL returned", flush=True)
                assert request("/api/shutdown", {})["ok"]
                assert process.wait(timeout=20) == 0
                log_path = home / "logs/app.log"
                log = log_path.read_text(encoding="utf-8")
                assert "Startup version=" in log and "Shutdown complete:" in log
                assert not (home / "instance.lock").exists()
                (ROOT / ".work/exe-smoke-app.log").write_text(log, encoding="utf-8")
                print("Shutdown: PASS exit=0 within 20 s, startup/shutdown logged, instance lock removed", flush=True)
                print("SMOKE PASSED", flush=True)
            finally:
                if process.poll() is None:
                    try:
                        request("/api/shutdown", {})
                        process.wait(timeout=20)
                    except Exception:
                        process.terminate()
                        process.wait(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
