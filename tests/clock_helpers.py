"""A fixed Europe/Amsterdam for 2026, so the schedule tests do not depend on the machine's tz database."""

from __future__ import annotations

from datetime import datetime, timedelta, tzinfo

_CET, _CEST = timedelta(hours=1), timedelta(hours=2)
_UTC_START = datetime(2026, 3, 29, 1, 0)
_UTC_END = datetime(2026, 10, 25, 1, 0)


class Amsterdam2026(tzinfo):
    """CET/CEST with 2026's EU switches (29 March 01:00 UTC, 25 October 01:00 UTC)."""

    def utcoffset(self, dt):
        if dt is None:
            return None
        wall = dt.replace(tzinfo=None)
        start = _UTC_START + _CET  # 02:00 wall: the hour that does not exist
        end = _UTC_END + _CEST  # 03:00 wall: after the repeated hour
        if wall < start:
            return _CET
        if wall < start + timedelta(hours=1):  # skipped hour: PEP 495, fold=0 keeps the old offset
            return _CEST if dt.fold else _CET
        if wall < end - timedelta(hours=1):
            return _CEST
        if wall < end:
            return _CET if dt.fold else _CEST
        return _CET

    def dst(self, dt):
        return self.utcoffset(dt) - _CET

    def tzname(self, dt):
        return "CEST" if self.utcoffset(dt) == _CEST else "CET"

    def fromutc(self, dt):
        utc = dt.replace(tzinfo=None)
        if _UTC_START <= utc < _UTC_END:
            return (utc + _CEST).replace(tzinfo=self)
        fold = 1 if _UTC_END <= utc < _UTC_END + timedelta(hours=1) else 0
        return (utc + _CET).replace(tzinfo=self, fold=fold)


def at(tz, text: str) -> datetime:
    """A wall-clock time ("2026-11-10 03:00") in zone `tz`."""
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
