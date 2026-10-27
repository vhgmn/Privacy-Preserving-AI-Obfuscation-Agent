"""Keeps the decoy Chrome on its own Windows virtual desktop, out of the user's way.

Chrome starts off-screen, its new window is moved to a desktop named DESKTOP_NAME
(created on first use, visible in Win+Tab) and then placed on that desktop's screen.
Playwright drives the page through the DevTools protocol, so the window never needs focus.
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

DESKTOP_NAME = "Decoy browsing"
OFFSCREEN_ARGS = ["--window-position=-32000,-32000", "--window-size=1280,900"]
# A window on another desktop counts as hidden; without these, Chrome would slow its pages down.
KEEP_RUNNING_ARGS = ["--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
                     "--disable-background-timer-throttling"]
_CHROME_CLASS = "Chrome_WidgetWin_1"
_SWP_NOSIZE, _SWP_NOZORDER, _SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010

_user32 = ctypes.windll.user32
_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def chrome_windows() -> set[int]:
    """Handles of all visible, titled top-level Chrome windows (the user's and ours)."""
    found: set[int] = set()
    buffer = ctypes.create_unicode_buffer(256)

    def visit(hwnd, _):
        if _user32.IsWindowVisible(hwnd) and _user32.GetWindowTextLengthW(hwnd) > 0:
            _user32.GetClassNameW(hwnd, buffer, 256)
            if buffer.value == _CHROME_CLASS:
                found.add(hwnd)
        return True

    _user32.EnumWindows(_ENUM_PROC(visit), 0)
    return found


def _decoy_desktop():
    import pyvda  # Windows-only, undocumented APIs: imported where it is needed

    for desktop in pyvda.get_virtual_desktops():
        if desktop.name == DESKTOP_NAME:
            return desktop
    desktop = pyvda.VirtualDesktop.create()
    desktop.rename(DESKTOP_NAME)
    return desktop


def _place(hwnd: int, x: int, y: int) -> None:
    _user32.SetWindowPos(hwnd, 0, x, y, 0, 0, _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE)


def hide_new_windows(before: set[int], wait_seconds: float = 10.0) -> str:
    """Moves Chrome windows that appeared since `before` to the decoy desktop.

    Returns a short status for the log. If moving fails, the window is brought on screen
    (visible) instead of being left stranded off-screen.
    """
    deadline = time.monotonic() + wait_seconds
    new: set[int] = set()
    while time.monotonic() < deadline and not new:
        new = chrome_windows() - before
        time.sleep(0.2)
    if not new:
        return "no new window found"
    try:
        import pyvda

        desktop = _decoy_desktop()
        for hwnd in new:
            pyvda.AppView(hwnd).move(desktop)
            _place(hwnd, 40, 40)
        return f"moved to desktop '{DESKTOP_NAME}'"
    except Exception as exc:  # pyvda raises COM errors of several kinds
        for hwnd in new:
            _place(hwnd, 40, 40)
        return f"could not move to another desktop ({type(exc).__name__}: {exc}); left visible"
