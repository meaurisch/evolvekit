"""`schedule` in study.yaml: windows in local wall-clock time."""

from __future__ import annotations

import re
from datetime import date, time, timedelta

import pytest
import yaml

from clock_helpers import Amsterdam2026, at
from evolvekit.harness import HarnessError
from evolvekit.harness.schedule import DAYS, PRESETS, Schedule, Window, when_words

TZ = Amsterdam2026()
NIGHTS = {"days": ["mon", "tue", "wed", "thu", "fri"], "from": "01:00", "to": "06:00"}
WEEKEND = {"days": ["sat", "sun"], "from": "00:00", "to": "23:59"}

try:
    from zoneinfo import ZoneInfo

    ZONES = [Amsterdam2026(), ZoneInfo("Europe/Amsterdam")]
except Exception:  # no tz database on this machine
    ZONES = [Amsterdam2026()]


def test_parse_windows_and_until():
    s = Schedule.parse({"windows": [NIGHTS, WEEKEND], "until": date(2026, 12, 31)})
    assert s.windows[0] == Window(frozenset({0, 1, 2, 3, 4}), time(1, 0), time(6, 0))
    assert s.until == date(2026, 12, 31)
    assert Schedule.parse({"windows": [NIGHTS], "until": "2026-12-31"}).until == date(2026, 12, 31)
    assert Schedule.parse(s.to_yaml()) == s
    dumped = yaml.safe_dump(s.to_yaml())
    assert yaml.safe_load(dumped)["windows"][0]["from"] == "01:00", "the dumper quotes it: read back as text"


def test_unquoted_times_read_as_yaml_writes_them():
    raw = yaml.safe_load("windows:\n  - {days: [sat], from: 01:00, to: 23:59}\n")
    assert raw["windows"][0]["to"] == 1439  # YAML 1.1 sexagesimal (PyYAML reads a leading-zero 01:00 as text)
    assert yaml.safe_load("from: 1:00")["from"] == 60
    window = Schedule.parse(raw).windows[0]
    assert (window.start, window.end) == (time(1, 0), time(23, 59))


@pytest.mark.parametrize("raw, said", [
    ({"windows": [{**WEEKEND, "days": ["sat", "funday"]}]},
     "schedule.windows[0].days: 'funday' is not a day; use mon, tue, wed, thu, fri, sat, sun"),
    ({"windows": [{**WEEKEND, "days": []}]}, "schedule.windows[0].days: name at least one day"),
    ({"windows": [{**NIGHTS, "from": "25:00"}]}, "schedule.windows[0].from: '25:00' is not a time of day such as 01:00"),
    ({"windows": [{**NIGHTS, "to": "24:00"}]}, "schedule.windows[0].to: use 23:59 for the end of the day"),
    ({"windows": [{**NIGHTS, "to": "01:00"}]}, "schedule.windows[0]: from and to are the same time; a window needs a length"),
    ({"windows": []}, "schedule.windows: name at least one window"),
    ({"windows": [NIGHTS], "until": "next week"}, "schedule.until: a date such as 2026-12-31"),
    ({"windows": [{**NIGHTS, "start": "01:00"}]}, "schedule.windows[0]: unknown key(s) ['start']"),
])
def test_refuses_bad_day_and_time(raw, said):
    with pytest.raises(HarnessError, match=re.escape(said)):
        Schedule.parse(raw)


def test_in_window_weekday():
    s = Schedule.parse({"windows": [NIGHTS, WEEKEND]})
    assert s.in_window(at(TZ, "2026-11-10 03:00"), tz=TZ) == s.windows[0]  # a Tuesday
    assert s.in_window(at(TZ, "2026-11-10 07:00"), tz=TZ) is None
    assert s.in_window(at(TZ, "2026-11-14 12:00"), tz=TZ) == s.windows[1]  # a Saturday


def test_window_straddles_midnight():
    s = Schedule.parse({"windows": [{"days": ["fri"], "from": "22:00", "to": "03:00"}]})
    assert s.in_window(at(TZ, "2026-11-14 02:00"), tz=TZ) is not None, "Saturday 02:00 belongs to Friday's window"
    assert s.in_window(at(TZ, "2026-11-13 02:00"), tz=TZ) is None, "Friday 02:00 would be Thursday's"
    assert s.span_at(at(TZ, "2026-11-14 02:00"), tz=TZ) == (at(TZ, "2026-11-13 22:00"), at(TZ, "2026-11-14 03:00"))


def test_adjacent_windows_merge():
    s = Schedule.parse({"windows": [WEEKEND]})
    moment = at(TZ, "2026-11-14 23:59") + timedelta(seconds=30)
    assert s.span_at(moment, tz=TZ) == (at(TZ, "2026-11-14 00:00"), at(TZ, "2026-11-15 23:59"))
    assert s.next_edge(moment, tz=TZ) == (at(TZ, "2026-11-15 23:59"), "end"), "no stop at midnight between Saturday and Sunday"


@pytest.mark.parametrize("zone", ZONES)
def test_dst_change_keeps_wall_clock(zone):
    s = Schedule.parse({"windows": [{"days": list(DAYS), "from": "01:00", "to": "06:00"}]})
    spring = s.span_at(at(zone, "2026-03-29 03:30"), tz=zone)
    assert spring[0].strftime("%H:%M") == "01:00" and spring[1].strftime("%H:%M") == "06:00"
    assert (spring[1].timestamp() - spring[0].timestamp()) == timedelta(hours=4).total_seconds()
    autumn = s.span_at(at(zone, "2026-10-25 04:00"), tz=zone)
    assert (autumn[1].timestamp() - autumn[0].timestamp()) == timedelta(hours=6).total_seconds()
    assert s.next_edge(at(zone, "2026-03-28 23:00"), tz=zone) == (at(zone, "2026-03-29 01:00"), "start")


def test_next_edge_from_outside_and_inside():
    s = Schedule.parse({"windows": [NIGHTS, WEEKEND]})
    assert s.next_edge(at(TZ, "2026-11-10 07:00"), tz=TZ) == (at(TZ, "2026-11-11 01:00"), "start")
    assert s.next_edge(at(TZ, "2026-11-10 03:00"), tz=TZ) == (at(TZ, "2026-11-10 06:00"), "end")
    assert s.next_edge(at(TZ, "2026-11-13 07:00"), tz=TZ) == (at(TZ, "2026-11-14 00:00"), "start")
    ending = Schedule.parse({"windows": [NIGHTS], "until": "2026-11-10"})
    assert ending.next_edge(at(TZ, "2026-11-10 07:00"), tz=TZ) is None
    assert ending.past_until(at(TZ, "2026-11-10 07:00"), tz=TZ)
    assert not ending.past_until(at(TZ, "2026-11-10 03:00"), tz=TZ)


def test_when_words():
    now = at(TZ, "2026-11-10 07:00")
    assert when_words(at(TZ, "2026-11-11 01:00"), now) == "Wed 01:00"
    assert when_words(at(TZ, "2026-11-19 01:00"), now) == "Thu 19 Nov 01:00"


def test_presets_parse():
    for preset in PRESETS.values():
        Schedule.parse(preset["schedule"])


def test_when_words_on_a_next_edge_across_midnight():
    s = Schedule.parse({"windows": [{"days": ["sun"], "from": "00:00", "to": "06:00"}]})
    now = at(TZ, "2026-11-10 07:00")
    edge = s.next_edge(now, tz=TZ)[0]
    assert edge.utcoffset() == timedelta(hours=1), "returned in the local zone, not UTC"
    assert when_words(edge, now) == "Sun 00:00"


def test_int_and_sexagesimal_times_parse():
    window = Schedule.parse({"windows": [{"days": ["sat"], "from": 60, "to": "06:00"}]}).windows[0]
    assert window.start == time(1, 0)
    raw = yaml.safe_load("windows:\n  - {days: [sat], from: 1:00, to: 6:00}\n")
    assert raw["windows"][0]["from"] == 60
    assert Schedule.parse(raw).windows[0] == Window(frozenset({5}), time(1, 0), time(6, 0))


@pytest.mark.parametrize("zone", ZONES)
def test_an_edge_in_the_skipped_spring_hour_moves_to_the_first_moment_after(zone):
    s = Schedule.parse({"windows": [{"days": list(DAYS), "from": "02:30", "to": "05:00"}]})
    start, end = s.span_at(at(zone, "2026-03-29 04:00"), tz=zone)
    assert start == at(zone, "2026-03-29 03:00")
    assert start.strftime("%H:%M") == "03:00"


@pytest.mark.parametrize("zone", ZONES)
def test_an_edge_in_the_repeated_autumn_hour_is_its_first_occurrence(zone):
    s = Schedule.parse({"windows": [{"days": list(DAYS), "from": "02:30", "to": "05:00"}]})
    start, end = s.span_at(at(zone, "2026-10-25 04:00"), tz=zone)
    first = at(zone, "2026-10-25 02:30")
    assert start.timestamp() == first.timestamp() and first.fold == 0
    assert start.utcoffset() == timedelta(hours=2), "CEST, the first 02:30"


def test_keep_awake_parses():
    assert Schedule.parse({"windows": [WEEKEND]}).keep_awake is True
    off = Schedule.parse({"windows": [WEEKEND], "keep_awake": False})
    assert off.keep_awake is False and off.to_yaml()["keep_awake"] is False
    assert "keep_awake: false" in yaml.safe_dump(off.to_yaml())
    assert Schedule.parse(off.to_yaml()) == off
    on = Schedule.parse({"windows": [WEEKEND], "keep_awake": True})
    assert "keep_awake" not in on.to_yaml(), "the default is not written"
    with pytest.raises(HarnessError, match="schedule.keep_awake: true or false"):
        Schedule.parse({"windows": [WEEKEND], "keep_awake": "no"})
    for preset in PRESETS.values():
        assert Schedule.parse(preset["schedule"]).keep_awake is True
