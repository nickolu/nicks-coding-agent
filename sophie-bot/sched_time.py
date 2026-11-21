"""Time arithmetic for Sophie's scheduler. Pure: no discord, no config, no I/O.

Why this module exists
----------------------
The scheduler stores fire times as UTC instants (`fire_at_utc`). Adding
`timedelta(days=1)` to a UTC instant is correct for *elapsed* time but wrong
for "same time tomorrow": across a DST transition a 09:00 PDT reminder drifts
to 08:00 PST. So:

- day-based recurrences (`daily`, `weekly`, `every:Nd`) are computed on the
  local wall clock in the bot's timezone, then converted to UTC;
- elapsed-time recurrences (`every:Nm`, `every:Nh`) stay on the UTC line.

The anchor: `nominal_local`
---------------------------
A recurring schedule carries `nominal_local`, the un-deferred local wall-clock
time it is *meant* to fire, as a naive ISO string ("2026-11-10T23:00:00")
interpreted in the bot's timezone. Recurrence advances from this anchor, not
from `fire_at_utc`, so two things stop drifting the schedule permanently:

- quiet-hours deferral rewrites `fire_at_utc` (e.g. 23:00 -> 08:00 next day)
  but never `nominal_local`, so the reminder returns to 23:00 the day after;
- the nonexistent spring-forward hour: a 02:30 anchor fires at 03:30 on the
  transition day but stays 02:30 for every other day.

Schedules written before this module have no `nominal_local`; they fall back
to `fire_at_utc` and gain the key the first time they advance. No migration.

DST details (America/Los_Angeles examples)
------------------------------------------
- Spring forward 2027-03-14: 02:30 does not exist. zoneinfo resolves the
  naive wall time with fold=0, i.e. using the pre-transition offset (PST),
  which lands on 03:30 PDT. The anchor is unchanged.
- Fall back 2026-11-01: 01:30 occurs twice. fold=0 picks the first (PDT)
  occurrence. Documented, not configurable.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

NOMINAL_KEY = "nominal_local"


# --- parsing -----------------------------------------------------------------

def parse_iso_aware(s: str, tz: ZoneInfo) -> datetime:
    """Parse an ISO 8601 timestamp into a UTC datetime; naive input is local `tz`."""
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


def parse_recurrence(rec: str | None) -> tuple[str, int] | None:
    """Return ("d"|"h"|"m", n) for a recurrence spec, None for no recurrence.

    Accepts "daily", "weekly", "every:Nm", "every:Nh", "every:Nd" (N >= 1).
    Raises ValueError with a user-facing message otherwise.
    """
    if not rec:
        return None
    if rec == "daily":
        return ("d", 1)
    if rec == "weekly":
        return ("d", 7)
    if rec.startswith("every:"):
        spec = rec[len("every:"):]
        if len(spec) >= 2 and spec[-1] in "mhd":
            try:
                n = int(spec[:-1])
            except ValueError:
                n = 0
            if n >= 1:
                return (spec[-1], n)
    raise ValueError(f"recurrence must be 'daily', 'weekly', or 'every:Nm|Nh|Nd' (got {rec!r})")


# --- local <-> UTC ------------------------------------------------------------

def local_to_utc(naive_local: datetime, tz: ZoneInfo) -> datetime:
    """Interpret a naive wall-clock time in `tz` (fold=0) and return UTC."""
    return naive_local.replace(tzinfo=tz, fold=0).astimezone(timezone.utc)


def to_local_naive(dt_utc: datetime, tz: ZoneInfo) -> datetime:
    """UTC (or any aware) datetime -> naive wall-clock time in `tz`."""
    return dt_utc.astimezone(tz).replace(tzinfo=None)


def local_naive_iso(dt_utc: datetime, tz: ZoneInfo) -> str:
    """Serialize a UTC instant as the naive local ISO string stored in `nominal_local`."""
    return to_local_naive(dt_utc, tz).replace(microsecond=0).isoformat(timespec="seconds")


def nominal_local(s: dict, tz: ZoneInfo) -> datetime:
    """The schedule's anchor as a naive local datetime.

    Uses `nominal_local` when present and parseable, else derives it from
    `fire_at_utc` (pre-anchor schedules, or schedules whose anchor got mangled).
    """
    raw = s.get(NOMINAL_KEY)
    if raw:
        try:
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                return dt
            return to_local_naive(dt, tz)
        except ValueError:
            pass
    return to_local_naive(parse_iso_aware(s["fire_at_utc"], tz), tz)


def ensure_anchor(s: dict, tz: ZoneInfo) -> bool:
    """Give a recurring schedule a `nominal_local` anchor if it has none.

    The anchor is derived from the current `fire_at_utc`, so call this *before*
    anything (quiet-hours deferral) rewrites `fire_at_utc`, otherwise the
    deferred time becomes the anchor and the schedule recurs at the wrong hour.
    Returns True if the key was added. Non-recurring schedules are left alone.
    """
    if not s.get("recurrence") or s.get(NOMINAL_KEY):
        return False
    s[NOMINAL_KEY] = nominal_local(s, tz).replace(microsecond=0).isoformat(timespec="seconds")
    return True


# --- recurrence ---------------------------------------------------------------

def _step(nominal: datetime, kind: str, n: int, tz: ZoneInfo) -> datetime:
    """One recurrence step from a naive local anchor; returns the new naive local anchor."""
    if kind == "d":
        # Naive arithmetic is pure wall-clock arithmetic: same HH:MM:SS, k days later.
        return nominal + timedelta(days=n)
    delta = timedelta(minutes=n) if kind == "m" else timedelta(hours=n)
    return to_local_naive(local_to_utc(nominal, tz) + delta, tz)


def next_fire(s: dict, tz: ZoneInfo) -> datetime | None:
    """UTC instant one recurrence step after the schedule's anchor; None if not recurring.

    Raises ValueError for a malformed recurrence spec.
    """
    spec = parse_recurrence(s.get("recurrence"))
    if spec is None:
        return None
    kind, n = spec
    return local_to_utc(_step(nominal_local(s, tz), kind, n, tz), tz)


def advance_schedule(s: dict, tz: ZoneInfo, now: datetime) -> bool:
    """Advance a recurring schedule in place to its first occurrence strictly after `now`.

    Always steps at least once (it is called right after a fire), then keeps
    stepping until `fire_at_utc > now`, so a schedule that was behind (bot
    downtime) catches up without re-firing every missed occurrence. Sets both
    `fire_at_utc` and `nominal_local`.

    Returns False and leaves `s` untouched when the schedule is not recurring.
    Raises ValueError for a malformed recurrence spec.
    """
    spec = parse_recurrence(s.get("recurrence"))
    if spec is None:
        return False
    kind, n = spec
    nominal = nominal_local(s, tz)

    if kind == "d":
        # Jump most of the way on the calendar, then settle with single steps.
        # Day counting is done on local dates so the wall clock is preserved.
        behind_days = (to_local_naive(now, tz).date() - nominal.date()).days
        if behind_days > n:
            nominal = nominal + timedelta(days=n * ((behind_days - 1) // n))
        nominal = _step(nominal, kind, n, tz)
        while local_to_utc(nominal, tz) <= now:
            nominal = _step(nominal, kind, n, tz)
        fire_utc = local_to_utc(nominal, tz)
    else:
        delta = timedelta(minutes=n) if kind == "m" else timedelta(hours=n)
        base = local_to_utc(nominal, tz)
        behind = now - base
        steps = 1
        if behind > delta:
            steps = behind // delta
        fire_utc = base + delta * steps
        while fire_utc <= now:
            fire_utc += delta
        nominal = to_local_naive(fire_utc, tz)

    s["fire_at_utc"] = fire_utc.isoformat()
    s[NOMINAL_KEY] = nominal.replace(microsecond=0).isoformat(timespec="seconds")
    return True
