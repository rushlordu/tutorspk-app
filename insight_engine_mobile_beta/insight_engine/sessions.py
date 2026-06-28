from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import List

# Windows sometimes has no IANA time-zone database for zoneinfo, which causes:
# ZoneInfoNotFoundError: No time zone found with key 'America/New_York'.
# We therefore try zoneinfo first, then pytz, then a safe UTC-offset fallback.
try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore
    ZoneInfoNotFoundError = Exception  # type: ignore

try:
    import pytz
except Exception:  # pragma: no cover
    pytz = None  # type: ignore


def _to_new_york(ts: datetime) -> datetime:
    """Convert UTC-aware datetime to New York time without crashing on Windows."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    # Preferred modern method. Works when tzdata is installed.
    if ZoneInfo is not None:
        try:
            return ts.astimezone(ZoneInfo("America/New_York"))
        except Exception:
            pass

    # Fallback already included in requirements.txt.
    if pytz is not None:
        try:
            return ts.astimezone(pytz.timezone("America/New_York"))
        except Exception:
            pass

    # Last-resort fallback: approximate NY time.
    # Crypto session tags do not need exact DST precision; this prevents GUI crash.
    return ts.astimezone(timezone(timedelta(hours=-5)))


def get_session_tags(ts: datetime | None = None) -> List[str]:
    """Return rough market-session tags for crypto timing context.

    Crypto is 24/7, but liquidity often shifts around regional opens.
    Times are intentionally broad windows, not exact trade signals.
    """
    if ts is None:
        ts = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    hour_utc = ts.astimezone(timezone.utc).hour
    tags: List[str] = []

    # Broad UTC windows.
    if 0 <= hour_utc < 6:
        tags.append("Asia")
    if 7 <= hour_utc < 11:
        tags.append("London open window")
    if 12 <= hour_utc < 16:
        tags.append("New York pre/open window")
    if 16 <= hour_utc < 21:
        tags.append("US afternoon")

    # Daily / weekly opens often matter in crypto.
    if ts.minute < 10 and hour_utc == 0:
        tags.append("Daily open")
    if ts.weekday() == 0 and hour_utc == 0 and ts.minute < 30:
        tags.append("Weekly open")

    # NYSE open at 09:30 New York. Tag a +-20 min window.
    ny = _to_new_york(ts)
    mins = ny.hour * 60 + ny.minute
    if (9 * 60 + 10) <= mins <= (9 * 60 + 50):
        tags.append("9:30 NY equity open")

    return tags or ["Neutral session"]
