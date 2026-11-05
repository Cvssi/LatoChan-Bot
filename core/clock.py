"""Configured wall-clock helpers shared by the modules."""
from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

from . import config as C


def local_now() -> datetime.datetime:
    return datetime.datetime.now(ZoneInfo(C.TIMEZONE))


def in_hours(start: int, end: int, hour: int | None = None) -> bool:
    """True if `hour` (default: now, configured local time) is in [start, end). Wraps midnight (22 -> 6); start == end is never."""
    if start == end:
        return False
    h = local_now().hour if hour is None else hour
    return start <= h < end if start < end else (h >= start or h < end)
