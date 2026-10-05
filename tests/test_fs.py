"""Filesystem path validation, isolated picker, and application preferences."""

import subprocess
import sys


def test_browse_validation_and_drives(client, library):
    roots = client.get("/api/fs/browse").json()
    assert roots and all(set(row) == {"name", "path"} for row in roots)
    rows = client.get("/api/fs/browse", params={"path": str(library)}).json()
    assert [row["name"] for row in rows] == ["Trips"]
    for value in ["relative", str(library / "nonexistent"), str(library / "Trips" / "..")]:
        result = client.get("/api/fs/browse", params={"path": value})
        assert result.status_code == 400
        assert "detail" in result.json()


def test_picker_child_process(client, library, monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout=str(library) + "\n", stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    assert client.post("/api/fs/pick").json() == {"path": str(library)}
    # The picker runs as an internal mode of the application itself, so the same argv shape
    # works for the source tree (-m photo_explorer --pick-folder) and the frozen executable.
    assert calls[0][0][0] == sys.executable and calls[0][0][-1] == "--pick-folder"
    assert calls[0][1]["timeout"] == 300
    assert not calls[0][1].get("shell")


def test_settings_health_and_local_protection(client, isolated_home):
    from photo_explorer import __version__
    health = client.get("/api/health").json()
    assert health.pop("last_backup").endswith('.db')
    assert health == {"ok": True, "version": __version__, "home": str(isolated_home.resolve())}
    prefs = {"theme": "light", "thumbSize": 250}
    assert client.put("/api/settings", json=prefs).json() == prefs
    assert client.get("/api/settings").json() == prefs
    assert client.get("/api/health", headers={"Host": "external.example"}).status_code == 403
    assert client.post("/api/fs/pick", headers={"Origin": "https://external.example"}).status_code == 403
    assert client.get("/").status_code == 200
