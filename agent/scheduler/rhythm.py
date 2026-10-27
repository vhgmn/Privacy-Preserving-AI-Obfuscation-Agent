"""The user's daily rhythm: in which hours they are at the computer.

Stored only locally (data/activity/rhythm.json): it describes the real person, so it is
as sensitive as browsing history and must never leave the machine.
"""
from __future__ import annotations

import ctypes
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from agent import config
from agent.scheduler.timing import DEFAULT_WAKING_HOURS

KEEP_DAYS = 28
MIN_DAYS_TO_LEARN = 7
WAKING_THRESHOLD = 0.3  # an hour counts as "awake" if the user was active in it on 30% of days


def idle_seconds() -> float:
    """Seconds since the last keyboard or mouse input (Windows GetLastInputInfo)."""

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    # both counters are milliseconds since boot; the 32-bit one wraps after ~49 days
    now = ctypes.windll.kernel32.GetTickCount64() & 0xFFFFFFFF
    return ((now - info.dwTime) & 0xFFFFFFFF) / 1000


class Rhythm:
    """For each recent day, the hours in which the user was active."""

    def __init__(self, days: dict[str, list[int]] | None = None):
        self.days = days or {}

    def record(self, now: datetime, active: bool) -> bool:
        """Returns True if something new was recorded (so the caller knows to save)."""
        if not active:
            return False
        hours = self.days.setdefault(now.date().isoformat(), [])
        if now.hour in hours:
            return False
        hours.append(now.hour)
        hours.sort()
        oldest = (now.date() - timedelta(days=KEEP_DAYS)).isoformat()
        for day in [d for d in self.days if d < oldest]:
            del self.days[day]
        return True

    def waking_hours(self, today: date) -> frozenset[int]:
        past = [hours for day, hours in self.days.items() if day < today.isoformat()]
        if len(past) < MIN_DAYS_TO_LEARN:
            return DEFAULT_WAKING_HOURS
        learned = frozenset(h for h in range(24) if sum(h in hours for hours in past) / len(past) >= WAKING_THRESHOLD)
        return learned or DEFAULT_WAKING_HOURS

    @staticmethod
    def path() -> Path:
        return config.DATA_DIR / "activity" / "rhythm.json"

    @classmethod
    def load(cls, path: Path | None = None) -> Rhythm:
        path = path or cls.path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        days = data.get("days") if isinstance(data, dict) else None
        valid = isinstance(days, dict) and all(
            isinstance(v, list) and all(isinstance(h, int) and 0 <= h < 24 for h in v) for v in days.values())
        return cls(days if valid else {})

    def save(self, path: Path | None = None) -> None:
        path = path or self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"days": self.days}, indent=1), encoding="utf-8")
        os.replace(tmp, path)
