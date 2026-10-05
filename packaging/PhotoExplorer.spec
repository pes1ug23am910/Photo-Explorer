# Folder distribution with local assets and native image decoders.
import importlib.util
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files

root = Path(SPECPATH).parent
sys.path.insert(0, str(root))
from photo_explorer import __version__

# Windows pillow-heif wheels place their dependent DLLs beside the extension.
heif_directory = Path(importlib.util.find_spec('_pillow_heif').origin).parent
binaries = collect_dynamic_libs('pillow_heif')
for pattern in ('libheif-*.dll', 'libde265-*.dll', 'libx265-*.dll', 'libgcc_s_seh-*.dll',
                'libstdc++-*.dll', 'libwinpthread-*.dll'):
    binaries.extend((str(path), '.') for path in heif_directory.glob(pattern))

a = Analysis(
    [str(root / 'packaging/entry.py')], pathex=[str(root)], binaries=binaries,
    datas=[(str(root / 'photo_explorer/static'), 'static')] + collect_data_files('tkinter'),
    hiddenimports=[
        'uvicorn.loops.auto', 'uvicorn.loops.asyncio',
        'uvicorn.protocols.http.auto', 'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.http.httptools_impl', 'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.websockets.websockets_impl', 'uvicorn.protocols.websockets.wsproto_impl',
        'uvicorn.lifespan.on', 'uvicorn.lifespan.off', 'uvicorn.logging',
        'pydantic', 'pydantic_core', 'fastapi', 'starlette', 'anyio._backends._asyncio',
        'pillow_heif', '_pillow_heif', 'PIL._tkinter_finder', 'tkinter', 'tkinter.filedialog',
        'watchdog.observers.read_directory_changes', 'pystray._win32', 'send2trash.win',
    ],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['numpy', 'scipy', 'imagehash', 'pytest', 'IPython', 'matplotlib'],
    noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='PhotoExplorer',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=True,
          icon=str(root / 'packaging/icon.ico'), version=str(root / 'packaging/version_info.txt'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='PhotoExplorer')
