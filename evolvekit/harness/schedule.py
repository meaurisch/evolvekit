"""`schedule` in `study.yaml`: when a study may run, in local wall-clock time.

A window names days, a start and an end ("mon-fri 01:00-06:00"); an end at or
before the start straddles midnight and belongs to the day it started on.
Occurrences are built from local dates and wall-clock times, so a daylight
saving change moves nothing: the window is 01:00-06:00 on that day too, an hour
shorter or longer in elapsed time. Spans are returned in UTC, compared in UTC.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any, Mapping

from evolvekit.harness import HarnessError

__all__ = [
    "DAYS", "DAY_WORDS", "JOIN_GAP_S", "MONTH_WORDS", "PRESETS", "Schedule", "Window", "when_words",
]

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_WORDS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTH_WORDS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
JOIN_GAP_S = 60.0
"""Two occurrences this close are one span (Saturday 23:59 and Sunday 00:00)."""

_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})$")
_UTC = timezone.utc


def _known(data: Mapping[str, Any], known: set[str], path: str) -> None:
    unknown = sorted(set(data) - known)
    if unknown:
        raise HarnessError(f"{path}: unknown key(s) {unknown}; known keys are {sorted(known)}")


def _parse_time(raw: Any, path: str) -> time:
    """`"H:MM"`, or an int of minutes: unquoted `01:00` is read by YAML 1.1 as 60."""
    if isinstance(raw, bool):
        minutes = None
    elif isinstance(raw, int):
        minutes = raw
    elif isinstance(raw, str):
        match = _CLOCK.match(raw.strip())
        minutes = 60 * int(match[1]) + int(match[2]) if match and int(match[2]) < 60 else None
        if match and int(match[1]) == 24 and int(match[2]) == 0:
            minutes = 1440
        elif match and int(match[1]) > 23:
            minutes = None
    else:
        minutes = None
    if minutes == 1440:
        raise HarnessError(f"{path}: use 23:59 for the end of the day")
    if minutes is None or not 0 <= minutes < 1440:
        raise HarnessError(f"{path}: {raw!r} is not a time of day such as 01:00")
    return time(minutes // 60, minutes % 60)


def _clock(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def _at(day: date, clock: time, tz: tzinfo | None) -> datetime:
    naive = datetime.combine(day, clock)
    return naive.replace(tzinfo=tz) if tz is not None else naive.astimezone()


def _aware(moment: datetime, tz: tzinfo | None) -> datetime:
    if moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=tz) if tz is not None else moment.astimezone()


@dataclass(frozen=True)
class Window:
    days: frozenset[int]
    start: time
    end: time

    @property
    def straddles(self) -> bool:
        return self.end <= self.start

    def to_yaml(self) -> dict[str, Any]:
        return {"days": [DAYS[i] for i in sorted(self.days)], "from": _clock(self.start), "to": _clock(self.end)}

    @staticmethod
    def parse(raw: Any, path: str) -> "Window":
        if not isinstance(raw, Mapping):
            raise HarnessError(f"{path}: expected a mapping with days, from and to, got {type(raw).__name__}")
        _known(raw, {"days", "from", "to"}, path)
        names = raw.get("days")
        if not isinstance(names, (list, tuple)) or not names:
            raise HarnessError(f"{path}.days: name at least one day")
        days: set[int] = set()
        for name in names:
            word = name.strip().lower() if isinstance(name, str) else None
            if word not in DAYS:
                raise HarnessError(f"{path}.days: {name!r} is not a day; use {', '.join(DAYS)}")
            days.add(DAYS.index(word))
        for key in ("from", "to"):
            if raw.get(key) is None:
                raise HarnessError(f"{path}.{key}: required, a time of day such as 01:00")
        start, end = _parse_time(raw["from"], f"{path}.from"), _parse_time(raw["to"], f"{path}.to")
        if start == end:
            raise HarnessError(f"{path}: from and to are the same time; a window needs a length")
        return Window(frozenset(days), start, end)


@dataclass(frozen=True)
class Schedule:
    windows: tuple[Window, ...]
    until: date | None = None

    @staticmethod
    def parse(raw: Any, path: str = "schedule") -> "Schedule":
        if not isinstance(raw, Mapping):
            raise HarnessError(f"{path}: expected a mapping with windows, got {type(raw).__name__}")
        _known(raw, {"windows", "until"}, path)
        listed = raw.get("windows")
        if not isinstance(listed, (list, tuple)) or not listed:
            raise HarnessError(f"{path}.windows: name at least one window")
        windows = tuple(Window.parse(w, f"{path}.windows[{i}]") for i, w in enumerate(listed))
        until = raw.get("until")
        if until is not None:
            if isinstance(until, datetime):
                until = until.date()
            elif isinstance(until, str):
                try:
                    until = date.fromisoformat(until.strip())
                except ValueError:
                    until = None
            elif not isinstance(until, date):
                until = None
            if until is None:
                raise HarnessError(f"{path}.until: a date such as 2026-12-31 (got {raw['until']!r})")
        return Schedule(windows, until)

    def to_yaml(self) -> dict[str, Any]:
        out: dict[str, Any] = {"windows": [w.to_yaml() for w in self.windows]}
        if self.until is not None:
            out["until"] = self.until
        return out

    # -- reading the clock -------------------------------------------------

    def _occurrences(self, around: datetime, tz: tzinfo | None) -> list[tuple[datetime, datetime, Window]]:
        local = _aware(around, tz).astimezone(tz) if tz is not None else _aware(around, tz).astimezone()
        first = local.date() - timedelta(days=2)
        found = []
        for offset in range(11):
            day = first + timedelta(days=offset)
            if self.until is not None and day > self.until:
                break
            for window in self.windows:
                if day.weekday() not in window.days:
                    continue
                end_day = day + timedelta(days=1) if window.straddles else day
                found.append((_at(day, window.start, tz).astimezone(_UTC), _at(end_day, window.end, tz).astimezone(_UTC), window))
        found.sort(key=lambda item: (item[0], item[1]))
        return found

    def spans(self, around: datetime, *, tz: tzinfo | None = None) -> list[tuple[datetime, datetime]]:
        merged: list[tuple[datetime, datetime]] = []
        for start, end, _ in self._occurrences(around, tz):
            if merged and (start - merged[-1][1]).total_seconds() <= JOIN_GAP_S:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged

    def span_at(self, now: datetime, *, tz: tzinfo | None = None) -> tuple[datetime, datetime] | None:
        moment = _aware(now, tz).astimezone(_UTC)
        for start, end in self.spans(now, tz=tz):
            if start <= moment < end:
                return start, end
        return None

    def in_window(self, now: datetime, *, tz: tzinfo | None = None) -> Window | None:
        moment = _aware(now, tz).astimezone(_UTC)
        for start, end, window in self._occurrences(now, tz):
            if start <= moment < end:
                return window
        return None

    def next_edge(self, now: datetime, *, tz: tzinfo | None = None) -> tuple[datetime, str] | None:
        held = self.span_at(now, tz=tz)
        if held is not None:
            return held[1], "end"
        moment = _aware(now, tz).astimezone(_UTC)
        for start, _ in self.spans(now, tz=tz):
            if start > moment:
                return start, "start"
        return None

    def past_until(self, now: datetime, *, tz: tzinfo | None = None) -> bool:
        return self.until is not None and self.next_edge(now, tz=tz) is None


PRESETS: dict[str, dict[str, Any]] = {
    "nights": {
        "label": "Nights, Monday to Friday, 01:00 to 06:00",
        "schedule": {"windows": [{"days": ["mon", "tue", "wed", "thu", "fri"], "from": "01:00", "to": "06:00"}]},
    },
    "weekends": {
        "label": "Weekends, all day",
        "schedule": {"windows": [{"days": ["sat", "sun"], "from": "00:00", "to": "23:59"}]},
    },
    "nights-and-weekends": {
        "label": "Nights, Monday to Friday, 01:00 to 06:00, and weekends",
        "schedule": {"windows": [
            {"days": ["mon", "tue", "wed", "thu", "fri"], "from": "01:00", "to": "06:00"},
            {"days": ["sat", "sun"], "from": "00:00", "to": "23:59"},
        ]},
    },
}


def when_words(moment: datetime, now: datetime) -> str:
    """`Tue 01:00` within six days of `now`, else `Tue 24 Nov 01:00`, in `moment`'s own zone."""
    clock = _clock(moment.time())
    day = DAY_WORDS[moment.weekday()]
    if abs(_aware(moment, None).astimezone(_UTC) - _aware(now, None).astimezone(_UTC)) <= timedelta(days=6):
        return f"{day} {clock}"
    return f"{day} {moment.day} {MONTH_WORDS[moment.month - 1]} {clock}"
