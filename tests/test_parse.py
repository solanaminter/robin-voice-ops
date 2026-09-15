"""Tests for api/_lib/parse.py — server-side entity extraction.

The LLM copies caller speech verbatim into string params; this module
deterministically parses service, date, time window, and phone digits.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from _lib.parse import parse_service, parse_time_window, parse_date, parse_phone


def test_parse_service_plumbing():
    assert parse_service("a plumber tomorrow morning for a leaking water heater") == "Plumbing"
    assert parse_service("my toilet is clogged") == "Plumbing"


def test_parse_service_hvac():
    assert parse_service("my AC is not cooling") == "HVAC"
    assert parse_service("furnace won't turn on") == "HVAC"


def test_parse_service_electrical():
    assert parse_service("sparking outlet in the kitchen") == "Electrical"


def test_parse_service_general_fallback():
    assert parse_service("just need someone to look at something") == "General"


def test_parse_time_window():
    assert parse_time_window("tomorrow morning") == "morning"
    assert parse_time_window("this afternoon") == "afternoon"
    assert parse_time_window("evening please") == "evening"
    assert parse_time_window("anytime works") == "any"


def test_parse_date_relative():
    tue = date(2026, 9, 15)  # a Tuesday
    assert parse_date("I need someone tomorrow morning", tue) == "2026-09-16"
    assert parse_date("today please", tue) == "2026-09-15"


def test_parse_date_day_after_tomorrow():
    tue = date(2026, 9, 15)  # a Tuesday
    # Regression: "day after tomorrow" contains the substring "tomorrow",
    # so the longer phrase must be checked first (+2 days, not +1).
    assert parse_date("can you come the day after tomorrow", tue) == "2026-09-17"


def test_parse_date_weekday():
    tue = date(2026, 9, 15)
    assert parse_date("how about friday", tue) == "2026-09-18"


def test_parse_date_none():
    assert parse_date("just wondering about prices") is None


def test_parse_phone_digits():
    assert parse_phone("415 555 0101") == "4155550101"
    assert parse_phone("my number is (415) 555-0101") == "4155550101"
    assert parse_phone("call me at 1-415-555-0101") == "4155550101"


def test_parse_phone_invalid():
    assert parse_phone("no number given") is None
    assert parse_phone("123") is None
