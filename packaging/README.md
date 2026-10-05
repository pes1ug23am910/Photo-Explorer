# Windows distribution maintenance

Use a 64-bit Windows Python environment at `.venv` with the runtime dependencies, pytest, httpx and PyInstaller installed. The folder build uses those installed packages and does not invoke UPX or an installer compiler. Run these commands sequentially from the checkout:

```powershell
$env:PHOTO_EXPLORER_HOME = "$PWD\.work\home"
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe packaging\smoke_exe.py --source
.\.venv\Scripts\python.exe packaging\build_exe.py
.\.venv\Scripts\python.exe packaging\smoke_exe.py
```

`build_exe.py` checks the interpreter, cleans only `build/` and `dist/PhotoExplorer/`, generates `icon.ico`, invokes PyInstaller once with a 15-minute timeout, and creates the versioned ZIP. Build logs stay in `build/pyinstaller.log`; caches and temporary files stay in `.work/`. The current `__version__` in `photo_explorer/__init__.py` drives package metadata, the spec and Windows version resource. `version_info.txt` evaluates that value when PyInstaller reads it; no second version literal needs updating.

The spec bundles the static tree at `_internal/static`, standard Tcl/Tk hooks, uvicorn dynamic modules, native watchers, tray/Recycle Bin backends, and the HEIF extension and dependent DLLs. `console=False` uses the windowed bootloader. Keep the complete folder together.

The smoke uses a temporary home and a copy of `.work/samples`, a minimal system PATH, and a working directory outside the source package. It requires `img1.jpg` and `phone.heic`, checks actual decoded derivative geometry and red sample pixels, then verifies diagnostics, export, rule preview, second-launch forwarding and graceful shutdown. It never opens a browser/tray or sends originals to the Recycle Bin. The optional `--source` preflight runs the same checks before building. The executable itself does not need Python installed.

`installer.iss` is an optional, uncompiled Inno Setup script. It reads the version from the built exe, installs per user to `%LOCALAPPDATA%\Programs\PhotoExplorer`, adds a Start-menu shortcut and registers an uninstaller. Uninstall preserves `%LOCALAPPDATA%\PhotoExplorer` so annotations and history are not silently discarded; remove that directory explicitly for a full reset. Inno Setup is not needed or invoked by these scripts.

Release builds are unsigned. SmartScreen reputation and antivirus behavior depend on the target machine. Check native browser focus, window close, tray interactions and installation manually; automated verification deliberately suppresses desktop UI.
