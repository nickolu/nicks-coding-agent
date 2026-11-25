"""Tests for sophie-tools/sophie-calendar-agenda (rendering + failure contract).

The tool has no .py suffix, so it is loaded with SourceFileLoader.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

TOOL = Path(__file__).resolve().parent.parent / "sophie-tools" / "sophie-calendar-agenda"
agenda = SourceFileLoader("sophie_calendar_agenda", str(TOOL)).load_module()

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 11, 21, 17, 14, tzinfo=timezone.utc)  # Sat Nov 21 09:14 PST


def timed(summary, start, end, **extra):
    ev = {"id": extra.pop("id", summary), "status": "confirmed", "summary": summary,
          "start": {"dateTime": start}, "end": {"dateTime": end}}
    ev.update(extra)
    return ev


def all_day(summary, start, end=None, **extra):
    ev = {"id": extra.pop("id", summary), "status": "confirmed", "summary": summary, "start": {"date": start}}
    if end:
        ev["end"] = {"date": end}
    ev.update(extra)
    return ev


def render(events, days=7, now=NOW):
    return agenda.render_agenda(events, now, LA, days)


# --- rendering ------------------------------------------------------------------------

def test_header_and_today_tomorrow_labels():
    out = render([], days=3)
    assert out.splitlines()[0] == "=== Calendar: next 3 days (as of Sat Nov 21 09:14 PST) ==="
    assert out.splitlines()[1] == "(no events)"
    out = render([timed("Dentist", "2026-11-22T10:00:00-08:00", "2026-11-22T11:00:00-08:00")], days=3)
    lines = out.splitlines()
    assert lines[1] == "Sat Nov 21 (today)"
    assert lines[2] == "  (nothing)"
    assert lines[3] == "Sun Nov 22 (tomorrow)"
    assert lines[4] == "  10:00-11:00  Dentist"
    assert lines[5] == "Mon Nov 23"


def test_timed_and_all_day_same_day_all_day_first_with_location_and_invite():
    events = [
        timed("Dentist", "2026-11-21T10:00:00-08:00", "2026-11-21T11:00:00-08:00", location="Main St\n Suite 2"),
        all_day("Finley's recital weekend", "2026-11-21", "2026-11-23"),
        timed("Team sync", "2026-11-21T14:00:00-08:00", "2026-11-21T14:30:00-08:00",
              attendees=[{"email": "a@b", "self": True, "responseStatus": "needsAction"}]),
    ]
    lines = render(events, days=2).splitlines()
    assert lines[2] == "  all-day      Finley's recital weekend"
    assert lines[3] == "  10:00-11:00  Dentist  @ Main St Suite 2"
    assert lines[4] == "  14:00-14:30  Team sync  (invite)"
    # Multi-day all-day (end exclusive): appears on Sat and Sun, not Mon.
    assert lines[6] == "  all-day      Finley's recital weekend"
    out3 = render(events, days=3).splitlines()
    assert out3[-2:] == ["Mon Nov 23", "  (nothing)"]


def test_utc_input_converted_to_local_and_long_location_dropped():
    ev = timed("Call", "2026-11-21T20:00:00Z", "2026-11-21T20:30:00Z", location="x" * 41)
    lines = render([ev], days=1).splitlines()
    assert lines[2] == "  12:00-12:30  Call"


def test_midnight_spanning_event_shows_on_both_days():
    ev = timed("Night shift", "2026-11-21T22:00:00-08:00", "2026-11-22T01:00:00-08:00")
    lines = render([ev], days=2).splitlines()
    assert lines[2] == "  22:00-01:00 (Sun) Night shift"
    assert lines[4] == "  until 01:00  Night shift"


def test_event_ending_exactly_at_midnight_is_not_shown_next_day():
    ev = timed("Late", "2026-11-21T22:00:00-08:00", "2026-11-22T00:00:00-08:00")
    lines = render([ev], days=2).splitlines()
    assert lines[2] == "  22:00-00:00  Late"
    assert lines[4] == "  (nothing)"


def test_cancelled_and_declined_skipped_no_title_filled():
    events = [
        timed("Gone", "2026-11-21T10:00:00-08:00", "2026-11-21T11:00:00-08:00", status="cancelled"),
        timed("Declined", "2026-11-21T10:00:00-08:00", "2026-11-21T11:00:00-08:00",
              attendees=[{"self": True, "responseStatus": "declined"}, {"self": False, "responseStatus": "accepted"}]),
        {"id": "x", "status": "confirmed", "start": {"dateTime": "2026-11-21T12:00:00-08:00"},
         "end": {"dateTime": "2026-11-21T12:15:00-08:00"}},
    ]
    lines = render(events, days=1).splitlines()
    assert lines[2:] == ["  12:00-12:15  (no title)"]


def test_summary_truncated_to_80_chars():
    ev = timed("A" * 100, "2026-11-21T10:00:00-08:00", "2026-11-21T11:00:00-08:00")
    line = render([ev], days=1).splitlines()[2]
    summary = line.split("  ", 2)[2]
    assert len(summary) == 80 and summary.endswith("…")


def test_duplicate_ids_rendered_once_and_sorted_by_start():
    a = timed("Later", "2026-11-21T15:00:00-08:00", "2026-11-21T16:00:00-08:00", id="1")
    b = timed("Earlier", "2026-11-21T09:00:00-08:00", "2026-11-21T09:30:00-08:00", id="2")
    lines = render([a, b, dict(a)], days=1).splitlines()
    assert lines[2:] == ["  09:00-09:30  Earlier", "  15:00-16:00  Later"]


def test_cap_at_60_lines_with_more_marker():
    events = [timed(f"E{i:02d}", f"2026-11-21T{8 + i // 6:02d}:{(i % 6) * 10:02d}:00-08:00",
                    f"2026-11-21T{8 + i // 6:02d}:{(i % 6) * 10 + 5:02d}:00-08:00", id=str(i)) for i in range(70)]
    lines = render(events, days=1).splitlines()
    assert len(lines) == 1 + 60 + 1
    assert lines[-1] == "  ... 11 more"


def test_fall_back_week_day_boundaries_use_zoneinfo():
    # 2026-11-01 has 25 hours. An event at 23:30 PST on Nov 1 must land on Nov 1,
    # and one at 00:30 PDT on Nov 1 must also be Nov 1 (naive +24h math would break this).
    now = datetime(2026, 10, 31, 16, 0, tzinfo=timezone.utc)  # Sat Oct 31 09:00 PDT
    events = [
        timed("Early", "2026-11-01T00:30:00-07:00", "2026-11-01T01:00:00-07:00"),
        timed("Late", "2026-11-01T23:30:00-08:00", "2026-11-01T23:45:00-08:00"),
        timed("Monday", "2026-11-02T00:15:00-08:00", "2026-11-02T00:45:00-08:00"),
    ]
    lines = render(events, days=3, now=now).splitlines()
    assert lines[1] == "Sat Oct 31 (today)" and lines[2] == "  (nothing)"
    assert lines[3] == "Sun Nov 01 (tomorrow)"
    assert lines[4:6] == ["  00:30-01:00  Early", "  23:30-23:45  Late"]
    assert lines[6:8] == ["Mon Nov 02", "  00:15-00:45  Monday"]


def test_spring_forward_week():
    now = datetime(2027, 3, 13, 17, 0, tzinfo=timezone.utc)  # Sat Mar 13 09:00 PST
    ev = timed("Brunch", "2027-03-14T10:00:00-07:00", "2027-03-14T11:00:00-07:00")
    lines = render([ev], days=2, now=now).splitlines()
    assert lines[0].endswith("(as of Sat Mar 13 09:00 PST) ===")
    assert lines[3:] == ["Sun Mar 14 (tomorrow)", "  10:00-11:00  Brunch"]


def test_to_json_normalizes():
    ev = timed("Dentist", "2026-11-21T10:00:00-08:00", "2026-11-21T11:00:00-08:00", htmlLink="https://x")
    rows = agenda.to_json([ev, all_day("Trip", "2026-11-22", "2026-11-24")], LA)
    assert rows[0]["summary"] == "Dentist" and rows[0]["all_day"] is False and rows[0]["html_link"] == "https://x"
    assert rows[0]["start"] == "2026-11-21T10:00:00-08:00"
    assert rows[1] == {**rows[1], "all_day": True, "start": "2026-11-22", "end": "2026-11-24"}


# --- gws output parsing --------------------------------------------------------------------

def test_parse_gws_output_pretty_and_ndjson():
    pretty = json.dumps({"items": [1]}, indent=2)
    assert agenda.parse_gws_output(pretty) == [{"items": [1]}]
    nd = '{"items": [1], "nextPageToken": "t"}\n{"items": [2], "nextSyncToken": "s"}\n'
    assert [p["items"] for p in agenda.parse_gws_output(nd)] == [[1], [2]]
    assert agenda.parse_gws_output("") == []
    assert agenda.parse_gws_output("garbage\nmore") == []


# --- failure contract -----------------------------------------------------------------------

def run_main(monkeypatch, capsys, fake, argv=("--days", "3")):
    monkeypatch.setattr(agenda, "run_gws", fake)
    rc = agenda.main(list(argv))
    out = capsys.readouterr().out
    return rc, out


def test_timeout_is_one_line_exit_0(monkeypatch, capsys):
    def fake(args, timeout=20):
        raise subprocess.TimeoutExpired(cmd="gws", timeout=timeout)
    rc, out = run_main(monkeypatch, capsys, fake)
    assert rc == 0 and out == "Calendar unavailable: gws timed out\n"


def test_auth_failure_rc2(monkeypatch, capsys):
    def fake(args, timeout=20):
        body = json.dumps({"error": {"code": 401, "message": "Access denied. No credentials provided.", "reason": "authError"}})
        return 2, body, "Using keyring backend: keyring\nerror[auth]: Access denied. Run gws auth login\n"
    rc, out = run_main(monkeypatch, capsys, fake)
    assert rc == 0 and out == "Calendar unavailable: gws not authenticated (see #16)\n"


def test_api_error_uses_message_truncated(monkeypatch, capsys):
    def fake(args, timeout=20):
        return 1, json.dumps({"error": {"code": 400, "message": "Bad " + "x" * 300}}), "error[api]: whatever\n"
    rc, out = run_main(monkeypatch, capsys, fake)
    assert rc == 0 and out.startswith("Calendar unavailable: gws error: 400 Bad x")
    assert len(out.splitlines()) == 1 and len(out) < 160


def test_nonzero_without_json_uses_stderr_first_real_line(monkeypatch, capsys):
    def fake(args, timeout=20):
        return 5, "", "Using keyring backend: keyring\nerror: something exploded\nsecret-ish second line\n"
    rc, out = run_main(monkeypatch, capsys, fake)
    assert out == "Calendar unavailable: gws error: something exploded\n"


def test_unparsable_stdout(monkeypatch, capsys):
    rc, out = run_main(monkeypatch, capsys, lambda args, timeout=20: (0, "not json at all", ""))
    assert rc == 0 and out == "Calendar unavailable: gws returned unparsable output\n"


def test_happy_path_passes_params_and_renders(monkeypatch, capsys):
    captured = {}

    def fake(args, timeout=20):
        captured["args"] = args
        page = {"items": [timed("Dentist", "2026-11-21T10:00:00-08:00", "2026-11-21T11:00:00-08:00")]}
        return 0, json.dumps(page) + "\n", "Using keyring backend: keyring\n"

    rc, out = run_main(monkeypatch, capsys, fake, argv=["--days", "2", "--tz", "America/Los_Angeles", "--now", "2026-11-21T17:14:00Z"])
    assert rc == 0
    assert out.splitlines()[0] == "=== Calendar: next 2 days (as of Sat Nov 21 09:14 PST) ==="
    assert "  10:00-11:00  Dentist" in out
    assert captured["args"][:3] == ["calendar", "events", "list"] and "--page-all" in captured["args"]
    params = json.loads(captured["args"][captured["args"].index("--params") + 1])
    assert params["timeMin"] == "2026-11-21T00:00:00-08:00" and params["timeMax"] == "2026-11-23T00:00:00-08:00"
    assert params["singleEvents"] is True and params["orderBy"] == "startTime"


def test_json_format(monkeypatch, capsys):
    def fake(args, timeout=20):
        return 0, json.dumps({"items": [timed("Dentist", "2026-11-21T10:00:00-08:00", "2026-11-21T11:00:00-08:00")]}), ""
    rc, out = run_main(monkeypatch, capsys, fake, argv=["--format", "json", "--tz", "America/Los_Angeles"])
    rows = json.loads(out)
    assert rows[0]["summary"] == "Dentist"


def test_resolve_tz_falls_back(monkeypatch):
    monkeypatch.setenv("TZ", "Not/AZone")
    assert agenda.resolve_tz("Also/Bogus").key == "America/Los_Angeles"
    monkeypatch.setenv("TZ", "America/New_York")
    assert agenda.resolve_tz(None).key == "America/New_York"
