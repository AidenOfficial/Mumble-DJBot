# coding=utf-8
"""botamusique core package: connection/commands (core), playback (player), startup."""
import os
import sys

# On Windows dev machines libopus is usually not installed system-wide, but
# the PyOgg wheel ships an opus.dll - make it discoverable on PATH so the
# Mumble library (pymumble-rev, which loads libopus via ctypes) can find it.
# Alternatively set PYMUMBLE_REV_LIBOPUS to the DLL's full path.
if sys.platform == "win32":
    import ctypes.util
    import sysconfig
    if ctypes.util.find_library("opus") is None:
        _pyogg_dir = os.path.join(sysconfig.get_paths()["purelib"], "pyogg")
        if os.path.isfile(os.path.join(_pyogg_dir, "opus.dll")):
            os.environ["PATH"] += os.pathsep + _pyogg_dir
