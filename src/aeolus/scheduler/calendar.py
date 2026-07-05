"""NSE trading calendar (TASK-013 ADR). Data comes from
scripts/fetch_nse_holidays.py's output (config/nse_holidays.json), fetched
from NSE's own live holiday-master endpoint -- never fabricated dates.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path

DEFAULT_OPEN = time(9, 15)
DEFAULT_CLOSE = time(15, 30)


def _default_holidays_path() -> Path:
    """Resolve config/nse_holidays.json for both repo checkouts and installed
    packages. Priority: AEOLUS_HOLIDAYS_PATH env var, then the repo-relative
    location (source tree), then config/ under the current working directory
    (installed package run from an app root, e.g. /app in the Fly image)."""
    env = os.getenv("AEOLUS_HOLIDAYS_PATH")
    if env:
        return Path(env)
    repo_relative = Path(__file__).resolve().parent.parent.parent.parent / "config" / "nse_holidays.json"
    if repo_relative.exists():
        return repo_relative
    return Path.cwd() / "config" / "nse_holidays.json"


DEFAULT_HOLIDAYS_PATH = _default_holidays_path()


@dataclass(frozen=True)
class SessionHours:
    open: time
    close: time


class NseCalendar:
    def __init__(self, holidays_path: str | Path = DEFAULT_HOLIDAYS_PATH) -> None:
        data = json.loads(Path(holidays_path).read_text())
        self._holidays = {date.fromisoformat(d) for d in data.get("holidays", [])}
        self._special_sessions = {
            date.fromisoformat(d): SessionHours(
                open=time.fromisoformat(hours["open"]), close=time.fromisoformat(hours["close"])
            )
            for d, hours in data.get("special_sessions", {}).items()
        }

    def is_trading_day(self, d: date) -> bool:
        return self.session_hours(d) is not None

    def session_hours(self, d: date) -> SessionHours | None:
        """A date can be both a full holiday and have a muhurat special
        session (Diwali) -- special_sessions is checked first."""
        if d in self._special_sessions:
            return self._special_sessions[d]
        if d in self._holidays:
            return None
        if d.weekday() >= 5:  # Saturday, Sunday
            return None
        return SessionHours(open=DEFAULT_OPEN, close=DEFAULT_CLOSE)
