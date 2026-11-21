"""DST-safety tests for sophie-bot/sched_time.py (America/Los_Angeles).

US transitions used:
- 2026-11-01 02:00 PDT -> 01:00 PST (fall back, 25-hour day)
- 2027-03-14 02:00 PST -> 03:00 PDT (spring forward, 23-hour day)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import sched_time as st

LA = ZoneInfo("America/Los_Angeles")
UTC = timezone.utc


def utc(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=UTC)


def local(y, mo, d, h=0, mi=0, s=0):
    """Aware LA datetime (fold=0)."""
    return datetime(y, mo, d, h, mi, s, tzinfo=LA)


def sched(fire_at_utc: datetime, recurrence, nominal: str | None = "auto") -> dict:
    s = {"id": "x", "recurrence": recurrence, "fire_at_utc": fire_at_utc.isoformat()}
    if nominal == "auto":
        s["nominal_local"] = st.local_naive_iso(fire_at_utc, LA)
    elif nominal is not None:
        s["nominal_local"] = nominal
    return s


def fire_at(s: dict) -> datetime:
    return datetime.fromisoformat(s["fire_at_utc"])


def local_wall(s: dict) -> str:
    return fire_at(s).astimezone(LA).strftime("%Y-%m-%d %H:%M %Z")


# --- parse_iso_aware ------------------------------------------------------------

def test_parse_iso_naive_is_local():
    assert st.parse_iso_aware("2026-11-05T09:00:00", LA) == utc(2026, 11, 5, 17, 0)


def test_parse_iso_z_and_offset():
    assert st.parse_iso_aware("2026-11-05T17:00:00Z", LA) == utc(2026, 11, 5, 17, 0)
    assert st.parse_iso_aware("2026-11-05T09:00:00-08:00", LA) == utc(2026, 11, 5, 17, 0)


# --- parse_recurrence -----------------------------------------------------------

@pytest.mark.parametrize("rec,expected", [
    (None, None),
    ("", None),
    ("daily", ("d", 1)),
    ("weekly", ("d", 7)),
    ("every:2d", ("d", 2)),
    ("every:90m", ("m", 90)),
    ("every:6h", ("h", 6)),
])
def test_parse_recurrence_ok(rec, expected):
    assert st.parse_recurrence(rec) == expected


@pytest.mark.parametrize("rec", ["hourly", "every:", "every:d", "every:xm", "every:0h", "every:5w"])
def test_parse_recurrence_bad(rec):
    with pytest.raises(ValueError):
        st.parse_recurrence(rec)


# --- daily / weekly across DST --------------------------------------------------

def test_daily_0900_across_fall_back_keeps_wall_clock():
    # Sat 2026-10-31 09:00 PDT = 16:00Z. Next day is PST: 09:00 PST = 17:00Z.
    s = sched(local(2026, 10, 31, 9), "daily")
    assert st.advance_schedule(s, LA, now=local(2026, 10, 31, 9)) is True
    assert fire_at(s) == utc(2026, 11, 1, 17, 0)
    assert local_wall(s) == "2026-11-01 09:00 PST"
    assert s["nominal_local"] == "2026-11-01T09:00:00"


def test_daily_0900_across_spring_forward_keeps_wall_clock():
    # Sat 2027-03-13 09:00 PST = 17:00Z. Next day is PDT: 09:00 PDT = 16:00Z.
    s = sched(local(2027, 3, 13, 9), "daily")
    st.advance_schedule(s, LA, now=local(2027, 3, 13, 9))
    assert fire_at(s) == utc(2027, 3, 14, 16, 0)
    assert local_wall(s) == "2027-03-14 09:00 PDT"


def test_daily_0230_spring_forward_fires_0330_then_returns_to_0230():
    # 02:30 does not exist on 2027-03-14; it resolves to 03:30 PDT (10:30Z).
    s = sched(local(2027, 3, 13, 2, 30), "daily")
    st.advance_schedule(s, LA, now=local(2027, 3, 13, 2, 30))
    assert fire_at(s) == utc(2027, 3, 14, 10, 30)
    assert local_wall(s) == "2027-03-14 03:30 PDT"
    # The anchor is untouched by the resolution, so the day after is 02:30 again.
    assert s["nominal_local"] == "2027-03-14T02:30:00"
    st.advance_schedule(s, LA, now=fire_at(s))
    assert local_wall(s) == "2027-03-15 02:30 PDT"
    assert fire_at(s) == utc(2027, 3, 15, 9, 30)


def test_fall_back_ambiguous_0130_uses_first_occurrence():
    # 01:30 happens twice on 2026-11-01; fold=0 -> PDT (-07:00) -> 08:30Z.
    s = sched(local(2026, 10, 31, 1, 30), "daily")
    st.advance_schedule(s, LA, now=local(2026, 10, 31, 1, 30))
    assert fire_at(s) == utc(2026, 11, 1, 8, 30)
    assert fire_at(s).astimezone(LA).utcoffset() == timedelta(hours=-7)


def test_weekly_across_fall_back_and_spring_forward():
    s = sched(local(2026, 10, 28, 18), "weekly")  # Wed 18:00 PDT = 01:00Z next day
    st.advance_schedule(s, LA, now=local(2026, 10, 28, 18))
    assert local_wall(s) == "2026-11-04 18:00 PST"
    assert fire_at(s) == utc(2026, 11, 5, 2, 0)

    s = sched(local(2027, 3, 10, 18), "weekly")
    st.advance_schedule(s, LA, now=local(2027, 3, 10, 18))
    assert local_wall(s) == "2027-03-17 18:00 PDT"
    assert fire_at(s) == utc(2027, 3, 18, 1, 0)


def test_every_2d_across_fall_back():
    s = sched(local(2026, 10, 30, 7), "every:2d")
    st.advance_schedule(s, LA, now=local(2026, 10, 30, 7))
    assert local_wall(s) == "2026-11-01 07:00 PST"
    assert fire_at(s) == utc(2026, 11, 1, 15, 0)


# --- elapsed-time intervals stay elapsed ------------------------------------------

def test_every_90m_across_fall_back_is_exactly_90_minutes():
    start = utc(2026, 11, 1, 7, 30)  # 00:30 PDT
    s = sched(start, "every:90m")
    st.advance_schedule(s, LA, now=start)
    assert fire_at(s) - start == timedelta(minutes=90)
    assert local_wall(s) == "2026-11-01 01:00 PST"


def test_every_6h_across_spring_forward_is_exactly_6_hours():
    start = utc(2027, 3, 14, 6, 0)  # 22:00 PST on the 13th
    s = sched(start, "every:6h")
    st.advance_schedule(s, LA, now=start)
    assert fire_at(s) - start == timedelta(hours=6)


# --- anchor semantics ---------------------------------------------------------------

def test_quiet_hours_deferral_keeps_2300_nominal():
    # Nominal 23:00 on Nov 10 (PST), deferred by quiet hours to 08:00 on Nov 11.
    deferred = local(2026, 11, 11, 8)
    s = sched(deferred, "daily", nominal="2026-11-10T23:00:00")
    st.advance_schedule(s, LA, now=deferred)
    assert local_wall(s) == "2026-11-11 23:00 PST"
    assert s["nominal_local"] == "2026-11-11T23:00:00"


def test_legacy_schedule_without_nominal_falls_back_to_fire_at():
    s = sched(local(2026, 11, 20, 9), "daily", nominal=None)
    assert "nominal_local" not in s
    st.advance_schedule(s, LA, now=local(2026, 11, 20, 9))
    assert local_wall(s) == "2026-11-21 09:00 PST"
    assert s["nominal_local"] == "2026-11-21T09:00:00"


def test_garbage_nominal_falls_back_to_fire_at():
    s = sched(local(2026, 11, 20, 9), "daily", nominal="not-a-date")
    st.advance_schedule(s, LA, now=local(2026, 11, 20, 9))
    assert local_wall(s) == "2026-11-21 09:00 PST"


def test_aware_nominal_is_normalized_to_local():
    s = sched(local(2026, 11, 20, 9), "daily", nominal="2026-11-20T17:00:00+00:00")
    st.advance_schedule(s, LA, now=local(2026, 11, 20, 9))
    assert local_wall(s) == "2026-11-21 09:00 PST"


# --- catch-up ---------------------------------------------------------------------------

def test_daily_catch_up_after_downtime_lands_on_first_future_same_wall_clock():
    s = sched(local(2026, 10, 20, 9), "daily")
    now = local(2026, 11, 3, 14, 15)  # 14 days later, across fall-back
    st.advance_schedule(s, LA, now=now)
    assert local_wall(s) == "2026-11-04 09:00 PST"


def test_daily_catch_up_when_now_is_same_day_before_the_hour():
    s = sched(local(2026, 11, 1, 9), "daily")
    now = local(2026, 11, 4, 8, 59)
    st.advance_schedule(s, LA, now=now)
    assert local_wall(s) == "2026-11-04 09:00 PST"


def test_weekly_catch_up_preserves_weekday():
    s = sched(local(2026, 9, 2, 18), "weekly")  # a Wednesday
    now = local(2027, 1, 15, 12)
    st.advance_schedule(s, LA, now=now)
    nxt = fire_at(s).astimezone(LA)
    assert nxt > now
    assert nxt.weekday() == 2 and (nxt.hour, nxt.minute) == (18, 0)
    assert nxt - now < timedelta(days=7)


def test_every_1m_catch_up_400_days_is_fast_and_within_one_minute():
    start = utc(2025, 10, 1, 0, 0, 30)
    s = sched(start, "every:1m")
    now = utc(2026, 11, 5, 12, 0, 0)
    st.advance_schedule(s, LA, now=now)
    assert now < fire_at(s) <= now + timedelta(minutes=1)
    assert fire_at(s).second == 30  # cadence anchored to the original seconds


def test_advance_is_strictly_after_now_even_when_exactly_on_a_boundary():
    s = sched(local(2026, 11, 20, 9), "every:1h")
    st.advance_schedule(s, LA, now=local(2026, 11, 20, 10))  # exactly one step ahead
    assert fire_at(s) == local(2026, 11, 20, 11).astimezone(UTC)


# --- next_fire / non-recurring / bad spec ----------------------------------------------

def test_next_fire_single_step():
    s = sched(local(2026, 10, 31, 9), "daily")
    assert st.next_fire(s, LA) == utc(2026, 11, 1, 17, 0)
    assert st.next_fire(sched(local(2026, 10, 31, 9), None), LA) is None


def test_non_recurring_untouched():
    s = sched(local(2026, 11, 20, 9), None, nominal=None)
    before = dict(s)
    assert st.advance_schedule(s, LA, now=local(2026, 11, 20, 9)) is False
    assert s == before


def test_bad_recurrence_raises():
    s = sched(local(2026, 11, 20, 9), "every:5w")
    with pytest.raises(ValueError):
        st.advance_schedule(s, LA, now=local(2026, 11, 20, 9))


def test_local_naive_iso_roundtrip():
    assert st.local_naive_iso(utc(2026, 11, 5, 17, 0, 0), LA) == "2026-11-05T09:00:00"
    assert st.local_to_utc(datetime(2026, 11, 5, 9, 0), LA) == utc(2026, 11, 5, 17, 0)


# --- ensure_anchor / legacy deferral --------------------------------------------------

def test_ensure_anchor_sets_from_fire_at_only_when_missing():
    s = sched(local(2026, 11, 10, 23), "daily", nominal=None)
    assert st.ensure_anchor(s, LA) is True
    assert s["nominal_local"] == "2026-11-10T23:00:00"
    # Idempotent: an existing anchor is never overwritten.
    s["fire_at_utc"] = local(2026, 11, 11, 8).isoformat()
    assert st.ensure_anchor(s, LA) is False
    assert s["nominal_local"] == "2026-11-10T23:00:00"


def test_ensure_anchor_ignores_non_recurring():
    s = sched(local(2026, 11, 10, 23), None, nominal=None)
    assert st.ensure_anchor(s, LA) is False
    assert "nominal_local" not in s


def test_legacy_schedule_deferred_after_deploy_returns_to_its_hour():
    # A pre-anchor 23:00 daily reminder hits quiet hours: the bot anchors it,
    # then overwrites fire_at with 08:00 next day (what scheduler_tick does).
    s = sched(local(2026, 11, 10, 23), "daily", nominal=None)
    st.ensure_anchor(s, LA)
    deferred = local(2026, 11, 11, 8)
    s["fire_at_utc"] = deferred.isoformat()
    st.advance_schedule(s, LA, now=deferred)
    assert local_wall(s) == "2026-11-11 23:00 PST"


def test_legacy_schedule_deferred_without_anchor_reproduces_old_bug():
    # Documents the unrecoverable case: already deferred before deploy, no anchor.
    deferred = local(2026, 11, 11, 8)
    s = sched(deferred, "daily", nominal=None)
    st.advance_schedule(s, LA, now=deferred)
    assert local_wall(s) == "2026-11-12 08:00 PST"  # hence the startup warning in bot.py
