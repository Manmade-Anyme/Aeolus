"""One-off/periodic refresh script (TASK-013 ADR) -- NOT run on the hot
scheduling path. Fetches NSE's public (undocumented) holiday-master endpoint
for the FO (Futures & Options) segment and writes config/nse_holidays.json
in the format aeolus.scheduler.calendar.NseCalendar expects.

Deliberately not fetched live at every session startup: this is an
undocumented endpoint (needs a homepage request first to pick up session
cookies -- a bare API call 403s) and a scraping dependency on the critical
"should I even start trading today" path is a worse failure mode than a
periodically-refreshed static file.

Run manually: whenever NSE publishes a new year's circular, and again a
week or so before Diwali to pick up muhurat special-session hours once
NSE announces them (they're null in the API response until then).

Usage: python scripts/fetch_nse_holidays.py [output_path]
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import httpx

HOLIDAY_ENDPOINT = "https://www.nseindia.com/api/holiday-master?type=trading"
HOMEPAGE = "https://www.nseindia.com"
SEGMENT = "FO"
HEADERS = {"user-agent": "PostmanRuntime/7.26.5"}
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "config" / "nse_holidays.json"


def fetch_fo_holidays() -> list[dict]:
    with httpx.Client(headers=HEADERS, timeout=10.0) as client:
        client.get(HOMEPAGE)  # picks up session cookies
        response = client.get(HOLIDAY_ENDPOINT)
        response.raise_for_status()
        return response.json()[SEGMENT]


def _parse_date(trading_date: str) -> str:
    return datetime.strptime(trading_date, "%d-%b-%Y").date().isoformat()


def build_calendar_json(raw_holidays: list[dict]) -> dict:
    holidays: list[str] = []
    special_sessions: dict[str, dict[str, str]] = {}

    for entry in raw_holidays:
        iso_date = _parse_date(entry["tradingDate"])
        holidays.append(iso_date)

        morning = entry.get("morning_session")
        evening = entry.get("evening_session")
        if morning and evening:
            special_sessions[iso_date] = {"open": morning, "close": evening}
        elif "*" in entry.get("description", ""):
            print(
                f"NOTE: {iso_date} ({entry['description']!r}) is flagged with a "
                "special-session marker but NSE hasn't published session hours "
                "yet -- re-run this script closer to the date.",
                file=sys.stderr,
            )

    return {"holidays": sorted(holidays), "special_sessions": special_sessions}


def main() -> None:
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    raw_holidays = fetch_fo_holidays()
    calendar_json = build_calendar_json(raw_holidays)
    output_path.write_text(json.dumps(calendar_json, indent=2) + "\n")
    print(f"Wrote {len(calendar_json['holidays'])} holidays to {output_path}")


if __name__ == "__main__":
    main()
