"""`schedule` in `study.yaml`: when a study may run, in local wall-clock time.

A window names days, a start and an end ("mon-fri 01:00-06:00"); an end at or
before the start straddles midnight and belongs to the day it started on.
Occurrences are built from local dates and wall-clock times, so a daylight
saving change moves nothing: the window is 01:00-06:00 on that day too, an hour
shorter or longer in elapsed time. Spans are compared and merged in UTC and returned in the local zone.
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
    minutes = None
    if isinstance(raw, int) and not isinstance(raw, bool):
        minutes = raw
    elif isinstance(raw, str):
        match = _CLOCK.match(raw.strip())
        if match and int(match[2]) < 60:
            minutes = 60 * int(match[1]) + int(match[2])
    if minutes == 1440:
        raise HarnessError(f"{path}: use 23:59 for the end of the day")
    if minutes is None or not 0 <= minutes < 1440:
        raise HarnessError(f"{path}: {raw!r} is not a time of day such as 01:00")
    return time(minutes // 60, minutes % 60)


def _clock(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def _at(day: date, clock: time, tz: tzinfo | None) -> datetime:
    """The wall-clock moment on `day`. One in the hour a spring-forward change
    skips moves to the first moment that exists after it; one in the repeated
    autumn hour is its first occurrence (fold=0)."""
    naive = datetime.combine(day, clock)
    if tz is None:
        return naive.astimezone()
    before = naive.replace(tzinfo=tz)
    early = before.astimezone(_UTC)
    if early.astimezone(tz).replace(tzinfo=None) == naive:
        return before
    later = naive.replace(tzinfo=tz, fold=1)
    moment = later.astimezone(_UTC)
    while moment.astimezone(tz).utcoffset() != later.utcoffset():
        moment += timedelta(minutes=1)
    return moment


def _local(moment: datetime, tz: tzinfo | None) -> datetime:
    return moment.astimezone(tz) if tz is not None else moment.astimezone()


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
    keep_awake: bool = True
    """A scheduled session keeps the computer from sleeping until its window ends, and through a
    final check that runs past it (D46); `keep_awake: false` turns that off."""

    @staticmethod
    def parse(raw: Any, path: str = "schedule") -> "Schedule":
        if not isinstance(raw, Mapping):
            raise HarnessError(f"{path}: expected a mapping with windows, got {type(raw).__name__}")
        _known(raw, {"windows", "until", "keep_awake"}, path)
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
        keep_awake = raw.get("keep_awake", True)
        if not isinstance(keep_awake, bool):
            raise HarnessError(f"{path}.keep_awake: true or false")
        return Schedule(windows, until, keep_awake)

    def to_yaml(self) -> dict[str, Any]:
        out: dict[str, Any] = {"windows": [w.to_yaml() for w in self.windows]}
        if self.until is not None:
            out["until"] = self.until
        if not self.keep_awake:
            out["keep_awake"] = False
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

    def _spans_utc(self, around: datetime, tz: tzinfo | None) -> list[tuple[datetime, datetime]]:
        merged: list[tuple[datetime, datetime]] = []
        for start, end, _ in self._occurrences(around, tz):
            if merged and (start - merged[-1][1]).total_seconds() <= JOIN_GAP_S:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged

    def spans(self, around: datetime, *, tz: tzinfo | None = None) -> list[tuple[datetime, datetime]]:
        """Every span from two days before `around`'s date to eight days after, as aware
        datetimes in the local zone (`tz`, else the computer's). Compute durations on
        `.astimezone(timezone.utc)` or `.timestamp()`: two datetimes in one zone subtract
        by wall clock."""
        return [(_local(a, tz), _local(b, tz)) for a, b in self._spans_utc(around, tz)]

    def span_at(self, now: datetime, *, tz: tzinfo | None = None) -> tuple[datetime, datetime] | None:
        moment = _aware(now, tz).astimezone(_UTC)
        for start, end in self._spans_utc(now, tz):
            if start <= moment < end:
                return _local(start, tz), _local(end, tz)
        return None

    def in_window(self, now: datetime, *, tz: tzinfo | None = None) -> Window | None:
        moment = _aware(now, tz).astimezone(_UTC)
        for start, end, window in self._occurrences(now, tz):
            if start <= moment < end:
                return window
        return None

    def next_edge(self, now: datetime, *, tz: tzinfo | None = None) -> tuple[datetime, str] | None:
        moment = _aware(now, tz).astimezone(_UTC)
        spans = self._spans_utc(now, tz)
        for start, end in spans:
            if start <= moment < end:
                return _local(end, tz), "end"
        for start, _ in spans:
            if start > moment:
                return _local(start, tz), "start"
        return None

    def past_until(self, now: datetime, *, tz: tzinfo | None = None) -> bool:
        return self.until is not None and self.next_edge(now, tz=tz) is None

    def week_seconds(self, now: datetime, *, tz: tzinfo | None = None) -> float:
        """The seconds the windows give in the week from `now` (`now` to seven days on), up to
        `until`: 47 h 59 min for weekends, 25 h for weeknights."""
        begin = _aware(now, tz).astimezone(_UTC)
        end = begin + timedelta(days=7)
        total = 0.0
        for start, stop in self._spans_utc(now, tz):
            low, high = max(start, begin), min(stop, end)
            if high > low:
                total += (high - low).total_seconds()
        return total

    def reach(self, now: datetime, seconds: float, *, tz: tzinfo | None = None
              ) -> tuple[datetime, datetime | None] | None:
        """Where `seconds` of window time from `now` begin and end, the windows taken in order:
        `(first moment, the moment the last second is spent)`, in the local zone; the end None
        when the windows up to `until` give less; None when no window is left at all."""
        cursor = _aware(now, tz).astimezone(_UTC)
        left = max(0.0, float(seconds))
        first: datetime | None = None
        for _ in range(1000):  # each look covers a week or more: twenty years of windows at most
            ahead = [(max(start, cursor), stop) for start, stop in self._spans_utc(cursor, tz) if stop > cursor]
            if not ahead:
                break
            for start, stop in ahead:
                if first is None:
                    first = start
                room = (stop - start).total_seconds()
                if room >= left:
                    return _local(first, tz), _local(start + timedelta(seconds=left), tz)
                left -= room
                cursor = stop
        return None if first is None else (_local(first, tz), None)


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
