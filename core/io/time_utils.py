# core/utils/time_utils.py
from __future__ import annotations

import datetime as _dt
import json
from typing import Optional
from urllib.request import urlopen
from urllib.error import URLError


def get_network_year(timeout: float = 2.0) -> Optional[int]:
    """
    Try to fetch current year from a network time service.

    Returns:
        int year if successful, otherwise None.
    """
    try:
        # Simple public time API, returns JSON with ISO datetime string.
        # You can swap this endpoint if needed.
        with urlopen("https://worldtimeapi.org/api/ip", timeout=timeout) as resp:
            data = json.load(resp)
        # Example datetime: "2025-12-04T10:23:45.123456+01:00"
        dt_str = data.get("datetime")
        if not dt_str:
            return None
        return int(dt_str[:4])
    except (URLError, ValueError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def get_current_year() -> int:
    """
    Get current year, preferring network time and falling back to local system time.
    """
    year = get_network_year()
    if year is not None:
        return year
    return _dt.datetime.now().year
