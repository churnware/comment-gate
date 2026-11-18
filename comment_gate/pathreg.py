"""Register `comment-gate` on PATH via launcher shims. The default
location is ~/.local/bin, a conventional per-user bin directory.
"""

import os
import stat
import sys
from pathlib import Path

from .installer import ROOT

SCRIPT = ROOT / "comment-gate.py"
NAME = "comment-gate"


def bin_dir():
    return Path(os.environ.get("COMMENT_GATE_BIN") or Path.home() / ".local" / "bin")


def shims():
    d = bin_dir()
    # On Windows, the .cmd serves cmd/PowerShell and the extensionless script serves Git Bash.
    return [d / f"{NAME}.cmd", d / NAME] if os.name == "nt" else [d / NAME]


def _norm(p):
    return os.path.normcase(os.path.abspath(os.path.expandvars(p))).rstrip("\\/")


def on_path(directory, path_value=None):
    entries = (path_value if path_value is not None else os.environ.get("PATH", "")).split(os.pathsep)
    return any(e and _norm(e) == _norm(directory) for e in entries)


def _user_path_windows():
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
        try:
            return winreg.QueryValueEx(k, "Path")[0]
        except FileNotFoundError:
            return ""


def _add_user_path_windows(directory):
    import ctypes
    import winreg

    current = _user_path_windows()
    if on_path(directory, current):
        return False
    value = f"{current.rstrip(';')};{directory}" if current else str(directory)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "Path", 0, winreg.REG_EXPAND_SZ, value)
    # Tell Explorer and new terminals that the environment changed.
    HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG = 0xFFFF, 0x1A, 0x2
    ctypes.windll.user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment",
                                             SMTO_ABORTIFHUNG, 2000, ctypes.byref(ctypes.c_ulong()))
    return True


def register():
    """Write the shims; returns (shim paths, path_status) where path_status is
    'present', 'added' or 'manual'."""
    d = bin_dir()
    d.mkdir(parents=True, exist_ok=True)
    py, script = Path(sys.executable), SCRIPT
    written = []
    for shim in shims():
        if shim.suffix == ".cmd":
            shim.write_bytes(f'@echo off\r\n"{py}" "{script}" %*\r\n'.encode("utf-8"))
        else:
            # Bytes, not text: a CRLF after the shebang breaks the script in Git Bash.
            shim.write_bytes(f'#!/bin/sh\nexec "{py.as_posix()}" "{script.as_posix()}" "$@"\n'.encode("utf-8"))
            shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        written.append(shim)

    if on_path(d) or (os.name == "nt" and on_path(d, _user_path_windows())):
        return written, "present"
    if os.name == "nt":
        _add_user_path_windows(d)
        return written, "added"
    return written, "manual"


def unregister():
    removed = []
    for shim in shims():
        if shim.exists():
            shim.unlink()
            removed.append(shim)
    return removed


def registered():
    return all(s.exists() for s in shims())
