"""Settings defaults, ranges and helpers shared by the JSON API and the settings page."""

from __future__ import annotations

from app.poller import DEFAULT_CAP_THRESHOLD
from app.scheduler import DEFAULT_FEED_INTERVAL_MIN, DEFAULT_SCAN_INTERVAL_MIN

INTERVAL_MIN, INTERVAL_MAX = 1, 1440
CAP_MIN, CAP_MAX = 1, 500

DEFAULTS = {
    "feed_interval_min": DEFAULT_FEED_INTERVAL_MIN,
    "scan_interval_min": DEFAULT_SCAN_INTERVAL_MIN,
    "feed_cap_threshold": DEFAULT_CAP_THRESHOLD,
}
RANGES = {
    "feed_interval_min": (INTERVAL_MIN, INTERVAL_MAX),
    "scan_interval_min": (INTERVAL_MIN, INTERVAL_MAX),
    "feed_cap_threshold": (CAP_MIN, CAP_MAX),
}


def mask_feed_url(url: str | None) -> str | None:
    return "…" + url[-4:] if url else None


def parse_setting_int(key: str, value: str | None) -> int | None:
    """Parse a form value for an integer setting; None when not a whole number in range."""
    low, high = RANGES[key]
    try:
        n = int((value or "").strip())
    except ValueError:
        return None
    return n if low <= n <= high else None


def range_error(label: str, key: str) -> str:
    low, high = RANGES[key]
    return f"{label} must be a whole number between {low} and {high}."
