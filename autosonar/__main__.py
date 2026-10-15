"""Start the AutoSonar tray app."""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

from autosonar.config import app_dir, migrate_legacy_config
from autosonar.ui import App

_INSTANCE_MUTEX: int | None = None
_ERROR_ALREADY_EXISTS = 183


def main() -> None:
    startup = "--startup" in sys.argv
    _configure_logging()
    if not _acquire_single_instance(quiet=startup):
        return
    migrate_legacy_config()
    logging.getLogger(__name__).info("AutoSonar is running")
    App(manage_startup=True, show_window=not startup).run()


def _configure_logging() -> None:
    handlers: list[logging.Handler] = []
    try:
        app_dir().mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(app_dir() / "autosonar.log", encoding="utf-8"))
    except OSError:
        pass
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers or None,
    )


def _acquire_single_instance(*, quiet: bool = False) -> bool:
    global _INSTANCE_MUTEX
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, "Local\\AutoSonar")
    error = ctypes.get_last_error()
    if not handle:
        return True
    if error == _ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        if not quiet:
            _say_already_running()
        return False
    _INSTANCE_MUTEX = int(handle)
    return True


def _say_already_running() -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    user32.MessageBoxW.restype = ctypes.c_int
    user32.MessageBoxW(
        None,
        "AutoSonar is already running. Look for it in the system tray.",
        "AutoSonar",
        0x40,
    )


if __name__ == "__main__":
    main()
