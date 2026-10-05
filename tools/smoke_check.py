"""Run a bounded loopback smoke check without opening a browser."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from make_samples import make_samples


def main() -> None:
    """Start the CLI, exercise HTTP navigation and image delivery, then terminate."""
    workspace = Path(__file__).resolve().parents[1]
    home = workspace / ".work/home"
    samples = workspace / ".work/samples"
    home.mkdir(parents=True, exist_ok=True)
    files = make_samples(samples, count=80, seed=7)
    print(f"Samples: {len(files)} visible images", flush=True)
    environment = {**os.environ, "PHOTO_EXPLORER_HOME": str(home), "PYTHONIOENCODING": "utf-8"}
    args = [sys.executable, "-m", "photo_explorer", "--no-browser", "--port", "8765", "--home", ".work/home"]
    process = subprocess.Popen(args, cwd=workspace, env=environment, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    base = "http://127.0.0.1:8765"

    def request(path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as response:
            payload = response.read()
            if response.headers.get_content_type() == "application/json":
                return json.loads(payload)
            return response.status, response.headers.get_content_type(), len(payload)

    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Server exited before readiness")
            try:
                health = request("/api/health")
                break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(.1)
        else:
            raise RuntimeError("Health readiness timed out")
        assert health["home"] == str(home)
        print("GET /api/health -> " + json.dumps(health), flush=True)
        root_id = request("/api/roots", {"path": str(samples)})["root"]["id"]
        print(f"POST /api/roots -> root {root_id}", flush=True)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            job = request(f"/api/roots/{root_id}/scan")
            if not job["running"]:
                break
            time.sleep(.1)
        assert not job["running"] and job["error"] is None, job
        print("Scan -> " + json.dumps({key: job[key] for key in ("phase", "seen", "indexed", "meta_done", "meta_total", "error")}), flush=True)
        images = request(f"/api/roots/{root_id}/images?sort=mtime&order=desc&limit=500")
        decodable = next(item for item in images["items"] if item["ext"] == "jpg" and item["meta_state"] == 1)
        for route in ("thumb?s=160", "preview", "file"):
            result = request(f"/api/images/{decodable['id']}/{route}")
            print(f"GET image {decodable['id']}/{route} -> status={result[0]} type={result[1]} bytes={result[2]}", flush=True)
            assert result[0] == 200 and result[2] > 0
        previous = float("inf")
        for index in range(5, 8):
            page = request(f"/api/roots/{root_id}/images?sort=mtime&order=desc&offset={index}&limit=1")
            item = page["items"][0]
            assert item["id"] == images["items"][index]["id"] and item["mtime"] <= previous
            previous = item["mtime"]
            print(f"Navigation index={index} id={item['id']} name={item['name']} mtime={item['mtime']}", flush=True)
        print("SMOKE PASSED", flush=True)
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            output, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate(timeout=5)
        print(f"Server output: {output.strip()}", flush=True)
        print("Server child terminated; no browser opened.", flush=True)


if __name__ == "__main__":
    main()
