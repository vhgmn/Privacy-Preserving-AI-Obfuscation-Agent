"""When to run the next session. Pure functions, so the policy can be tested without waiting."""
from __future__ import annotations

import math
import random
from dataclasses import replace
from datetime import datetime, timedelta

from agent.planner.schema import Session

ACTIVE_IDLE_SECONDS = 120  # input in the last 2 minutes = the user is at the computer
IDLE_SESSION_SHARE = 0.3  # share of due sessions that run while the user is away
MEDIAN_GAP_MINUTES, GAP_SIGMA = 40, 0.9
MIN_GAP_MINUTES, MAX_GAP_MINUTES = 5, 240
DEFAULT_WAKING_HOURS = frozenset(range(8, 23))  # 08:00-23:00 until the user's rhythm is learned


def is_active(idle_seconds: float) -> bool:
    return idle_seconds < ACTIVE_IDLE_SECONDS


def next_gap(rng: random.Random, remaining_sessions: int, remaining_waking_minutes: float) -> timedelta:
    """Log-normal pause until the next session: mostly short, sometimes hours, like real browsing.
    The median shrinks when many sessions are left for the rest of the day."""
    median = MEDIAN_GAP_MINUTES
    if remaining_sessions > 0:
        median = min(median, max(MIN_GAP_MINUTES, remaining_waking_minutes / (remaining_sessions + 1)))
    minutes = rng.lognormvariate(math.log(median), GAP_SIGMA)
    return timedelta(minutes=min(MAX_GAP_MINUTES, max(MIN_GAP_MINUTES, minutes)))


def remaining_waking_minutes(now: datetime, waking_hours: frozenset[int]) -> float:
    minutes = 60 - now.minute if now.hour in waking_hours else 0
    return minutes + 60 * sum(1 for h in range(now.hour + 1, 24) if h in waking_hours)


def decide(now: datetime, idle_seconds: float, waking_hours: frozenset[int], next_due: datetime | None,
           rng: random.Random) -> str | None:
    """'full', 'short' or None (not now).

    Most sessions run while the user is active, so their real traffic is mixed with decoys.
    When they are away (but in their waking hours) a due session sometimes runs short.
    """
    if now.hour not in waking_hours:
        return None
    if next_due is not None and now < next_due:
        return None
    if is_active(idle_seconds):
        return "full"
    return "short" if rng.random() < IDLE_SESSION_SHARE else None


def short_version(session: Session) -> Session:
    """One search, one result, one read: what someone does in a quick look while passing by."""
    return replace(session, steps=session.steps[:3])
