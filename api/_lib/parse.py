"""Backend entity extraction for Robin Voice Ops.

The Voice Agent LLM is reliable at intent classification (which tool to call)
and at copying caller speech verbatim into string parameters, but unreliable
at normalizing speech into enums/formatted values. So tools take free-text
strings and this module does the parsing server-side, where it is
deterministic and testable.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

SERVICE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("Plumbing", ["plumb", "leak", "drip", "pipe", "water heater", "faucet",
                  "toilet", "drain", "clog", "sewer", "sink", "shower"]),
    ("HVAC", ["hvac", "ac ", "a/c", "air condition", "furnace", "heater",
              "heating", "cooling", "thermostat", "vent", "duct"]),
    ("Electrical", ["electric", "wiring", "outlet", "breaker", "panel",
                    "sparking", "spark", "light fixture", "ceiling fan"]),
]

TIME_WINDOW_KEYWORDS: list[tuple[str, list[str]]] = [
    ("morning", ["morning", "am ", "a.m.", "8am", "9am", "10am", "11am", "before noon"]),
    ("afternoon", ["afternoon", "pm ", "p.m.", "12pm", "1pm", "2pm", "3pm", "4pm", "after noon"]),
    ("evening", ["evening", "night", "5pm", "6pm", "7pm", "after 5", "after work"]),
]

DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_service(text: str) -> str:
    t = f" {text.lower()} "
    for service, keywords in SERVICE_KEYWORDS:
        if any(k in t for k in keywords):
            return service
    return "General"


def parse_time_window(text: str) -> str:
    t = f" {text.lower()} "
    for window, keywords in TIME_WINDOW_KEYWORDS:
        if any(k in t for k in keywords):
            return window
    return "any"


def parse_date(text: str, today: date | None = None) -> str | None:
    """Resolve relative day references to YYYY-MM-DD. Returns None if no date found."""
    today = today or date.today()
    t = text.lower()
    if "today" in t:
        return today.isoformat()
    # NB: "day after tomorrow" contains "tomorrow" — check the longer phrase first.
    if "day after tomorrow" in t:
        return (today + timedelta(days=2)).isoformat()
    if "tomorrow" in t:
        return (today + timedelta(days=1)).isoformat()
    # Explicit YYYY-MM-DD or MM/DD
    m = re.search(r"\b(20\d\d)-(\d\d)-(\d\d)\b", t)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(20\d\d))?\b", t)
    if m:
        y = m.group(3) or str(today.year)
        return f"{y}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    # Weekday names -> next occurrence
    for i, day in enumerate(DAY_NAMES):
        if day in t:
            delta = (i - today.weekday()) % 7 or 7
            return (today + timedelta(days=delta)).isoformat()
    return None


def parse_phone(text: str) -> str | None:
    """Extract a 10-digit US phone number from free text. Returns digits or None."""
    digits = re.sub(r"\D", "", text)
    # Strip leading country code
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None
