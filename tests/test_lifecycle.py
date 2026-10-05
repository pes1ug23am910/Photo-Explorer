"""Desktop lifetime without real clocks, browsers, or tray icons."""

import subprocess

from photo_explorer.lifecycle import Lifecycle
from photo_explorer.launcher import WindowManager, open_window


def controller(window=True, keep=False):
    clock = [0.0]
    life = Lifecycle(window, keep, lambda: clock[0])
    reasons = []
    life.on_shutdown = reasons.append
    return clock, life, reasons


def test_reload_heartbeat_cancels_grace():
    clock, life, reasons = controller()
    life.window_closed()
    clock[0] = 9
    life.tick()
    assert not reasons
    life.heartbeat()
    clock[0] = 12
    life.tick()
    assert not reasons
    life.window_closed()
    clock[0] = 22
    life.tick()
    life.tick()
    assert reasons == ["window closed (grace expired)"]


def test_heartbeat_timeout_allows_hidden_tab_throttling():
    clock, life, reasons = controller()
    for stamp in (60, 120, 180):
        clock[0] = stamp
        life.tick()
        assert not reasons
        life.heartbeat()
    clock[0] = 329.9
    life.tick()
    assert not reasons
    clock[0] = 330
    life.tick()
    assert reasons == ["heartbeat timeout"]


def test_no_browser_never_auto_exits():
    clock, life, reasons = controller(False)
    life.window_closed()
    life.process_closed()
    clock[0] = 10000
    life.tick()
    assert not reasons
    life.shutdown("explicit quit")
    assert reasons == ["explicit quit"]


def test_keep_running_and_owned_process_exit():
    clock, life, reasons = controller(keep=True)
    life.process_closed()
    life.window_closed()
    clock[0] = 1000
    life.tick()
    assert not reasons
    life.set_keep_running(False)
    life.process_closed()
    assert reasons == ["window closed"]


def test_shutdown_refuses_foreign_origin_and_fetch_site(client):
    life = client.app.state.lifecycle
    for headers in ({"Origin": "https://example.com"}, {"Origin": "http://127.0.0.1:9999"},
                    {"Sec-Fetch-Site": "cross-site"}):
        assert client.post("/api/shutdown", headers=headers).status_code == 403
        assert life.reason is None
    assert client.post("/api/heartbeat").json()["ok"]
    assert client.post("/api/window-closed").json()["ok"]
    life.tick()
    assert life.reason is None
    assert client.post("/api/shutdown", headers={"Origin": "http://testserver"}).json()["ok"]
    assert life.reason == "Quit requested through API"


def test_browser_argv_and_owned_handle(tmp_path, monkeypatch):
    import photo_explorer.launcher as launcher
    calls = []
    handle = object()
    monkeypatch.setattr(launcher, "browser_path", lambda choice: tmp_path / "edge.exe")
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: calls.append((argv, kw)) or handle)
    assert open_window("http://127.0.0.1:9999/", "edge", tmp_path) is handle
    argv, kw = calls[0]
    assert f"--user-data-dir={tmp_path / 'browser-profile'}" in argv
    assert "--app=http://127.0.0.1:9999/" in argv
    assert "--window-size=1500,950" in argv
    assert "--no-first-run" in argv and "--no-default-browser-check" in argv
    assert not kw.get("shell")


def test_window_wait_signals_exit_without_ui(tmp_path):
    _, life, reasons = controller()
    manager = WindowManager("http://127.0.0.1:1234/", tmp_path, "auto", life)
    class Process:
        def wait(self, timeout):
            assert timeout == 1
            return 0
    manager.process = Process()
    manager._wait(manager.process)
    assert manager.process is None and reasons == ["window closed"]


def test_focus_reuses_handle_and_routes_diagnostics(tmp_path, monkeypatch):
    _, life, _ = controller()
    manager = WindowManager("http://127.0.0.1:1234/", tmp_path, "auto", life)
    class Process:
        def poll(self):
            return None
    manager.process = Process()
    calls = []
    monkeypatch.setattr(manager, "_focus", lambda: calls.append(True))
    manager.open("#diagnostics")
    assert calls == [True]
    assert manager.page_command == {"sequence": 1, "fragment": "#diagnostics"}


def test_tray_callbacks_without_native_icon(monkeypatch):
    import sys
    from types import SimpleNamespace
    from photo_explorer import tray
    _, life, _ = controller()
    calls = []
    monkeypatch.setattr(tray, "request_json", lambda *args, **kw: calls.append((args, kw)))
    class Icon:
        def __init__(self, name, image, title, menu):
            self.menu = menu
            self.title = title
            image.close()
            self.stopped = False
        def run_detached(self, setup):
            setup(self)
        def update_menu(self):
            pass
        def stop(self):
            self.stopped = True
    def item(label, action, **kwargs):
        return SimpleNamespace(label=label, action=action, **kwargs)
    monkeypatch.setitem(sys.modules, "pystray", SimpleNamespace(Icon=Icon, Menu=lambda *items: items, MenuItem=item))
    service = tray.Tray(9876, life)
    service._start()
    icon = service.icon
    assert icon.visible and icon.title == "Photo Explorer — 9876"
    icon.menu[0].action()
    icon.menu[1].action(icon, icon.menu[1])
    assert life.keep_running and icon.menu[1].checked(None)
    icon.menu[2].action()
    icon.menu[3].action()
    assert [args[1] for args, _ in calls] == ["/api/focus", "/api/focus?diagnostics=true", "/api/shutdown"]
    service.close()
    assert icon.stopped


def test_tray_failure_is_nonfatal(monkeypatch):
    import sys
    from types import SimpleNamespace
    from photo_explorer import tray
    _, life, _ = controller()
    def broken(*args, **kwargs):
        raise OSError("notification area unavailable")
    monkeypatch.setitem(sys.modules, "pystray", SimpleNamespace(Icon=broken, Menu=broken, MenuItem=broken))
    service = tray.Tray(9876, life)
    service._start()
    assert service.icon is None and not life.stopping.is_set()


def test_current_health_settings_and_local_protection(client):
    from photo_explorer import __version__
    health = client.get("/api/health").json()
    assert health["version"] == __version__ and health["last_backup"].endswith(".db")
    prefs = {"theme": "light", "thumbSize": 250, "watchFolders": False}
    assert client.put("/api/settings", json=prefs).json() == prefs
    assert client.get("/api/settings").json() == prefs
    assert client.get("/api/health", headers={"Host": "external.example"}).status_code == 403
    assert client.post("/api/fs/pick", headers={"Origin": "https://external.example"}).status_code == 403
    assert client.get("/").status_code == 200
