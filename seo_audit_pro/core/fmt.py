# -*- coding: utf-8 -*-
"""Display formatting helpers (dates in local time, sizes, labels)."""

from datetime import datetime, timezone


def parse(iso):
    if not iso:
        return None
    try:
        s = str(iso).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone()
    except ValueError:
        return None


def date(iso, empty="—"):
    dt = parse(iso)
    return dt.strftime("%d %b %Y") if dt else empty


def datetime_(iso, empty="—"):
    dt = parse(iso)
    return dt.strftime("%d %b %Y, %H:%M") if dt else empty


def time_(iso):
    dt = parse(iso)
    return dt.strftime("%H:%M") if dt else ""


def relative(iso, empty="—"):
    dt = parse(iso)
    if not dt:
        return empty
    secs = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
    if secs < 45:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60) or 1} min ago"
    if secs < 86400:
        return f"{int(secs // 3600)} h ago"
    if secs < 7 * 86400:
        days = int(secs // 86400)
        return "yesterday" if days == 1 else f"{days} days ago"
    return date(iso)


def duration(start_iso, end_iso):
    a, b = parse(start_iso), parse(end_iso)
    if not a or not b:
        return "—"
    secs = int((b - a).total_seconds())
    if secs < 60:
        return f"{secs}s"
    return f"{secs // 60}m {secs % 60:02d}s"


def eta(seconds):
    """Rough time left, e.g. "about 25 min", "about 3 h 10 min"."""
    if seconds is None:
        return "—"
    mins = int(round((seconds or 0) / 60))
    if mins < 1:
        return "under a minute"
    if mins < 60:
        return f"about {mins} min"
    h, m = divmod(mins, 60)
    return f"about {h} h {m:02d} min" if m else f"about {h} h"


def size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def plural(n, word, plural_word=None):
    return f"{n} {word if n == 1 else (plural_word or word + 's')}"


def number(n):
    if n is None:
        return "—"
    return f"{int(n):,}"


def action_label(action):
    """AUDIT_COMPLETED -> Audit completed"""
    return (action or "").replace("_", " ").capitalize()
