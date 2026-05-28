from __future__ import annotations

import os
from pathlib import Path


def configure_qt_runtime() -> None:
    """Force PyQt5 to load Qt DLLs/plugins from this project's venv.

    Some Windows machines have another PyQt/Qt path in PATH. If QtCore loads a
    DLL from that foreign environment, PyQt5 raises:
    "DLL load failed while importing QtCore: %1 is not a valid Win32 application".
    """
    try:
        import PyQt5
    except Exception:
        return

    pyqt_dir = Path(PyQt5.__file__).resolve().parent
    qt_root = pyqt_dir / "Qt5"
    qt_bin = qt_root / "bin"
    platforms = qt_root / "plugins" / "platforms"

    if qt_bin.exists():
        os.environ["PATH"] = str(qt_bin) + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(qt_bin))

    if platforms.exists():
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(platforms)

    # Avoid inheriting a global plugin path from another Python/Qt install.
    os.environ.pop("QT_PLUGIN_PATH", None)
