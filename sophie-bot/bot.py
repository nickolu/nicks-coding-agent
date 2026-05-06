#!/usr/bin/env python3
"""Sophie <> Discord bridge. Forwards DMs from an allowed user to sophie-sandbox -p.

Sophie is Nick's chief of staff / personal manager (see sophie-config/CLAUDE.md).
This bot is the same shape as the Howl bot but stripped of the autonomous-coding
loop and CI watcher features — Sophie doesn't open PRs, so she doesn't need them.

The watcher framework is kept (and renamed in the user-facing help) because it's
generically useful for time-based reminders and polling Google Tasks / Calendar.
"""
import asyncio
import base64
import io
import json
import logging
import os
import socket
import subprocess
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import discord

CONFIG_PATH = Path("/etc/sophie-discord/config.json")
STATE_DIR = Path("/var/lib/sophie-discord")
STATE_PATH = STATE_DIR / "sessions.json"
SCHEDULES_PATH = STATE_DIR / "schedules.json"
RUNTIME_PATH = STATE_DIR / "runtime.json"
INBOUND_LOG = STATE_DIR / "inbound-messages.jsonl"
OUTBOUND_LOG = STATE_DIR / "outbound-messages.jsonl"
REACTIONS_LOG = STATE_DIR / "reactions.jsonl"
LAST_TICK_PATH = STATE_DIR / "last-tick.json"
TRIPWIRE_LOG = Path("/home/sophie/.claude-container/tripwire.log")
SOPHIE_CMD = "/usr/local/bin/sophie-sandbox"
NOTIFY_SOCKET = "/run/sophie-discord.sock"
SOPHIE_TIMEOUT = None  # no timeout
MAX_WATCHER_TIMEOUT = 1440  # 24 hours in minutes
DEFAULT_WATCHER_TIMEOUT = 60  # 1 hour in minutes

# Scheduler
SCHEDULER_TICK_SEC = 30
MISSED_FIRE_GRACE_SEC = 6 * 3600  # fire late if behind by less than this; else send "missed" notice
MAX_SCHEDULE_HORIZON_DAYS = 365

# Circuit breaker — auto-disable autonomy if Sophie sends too many unsolicited messages
DM_RATE_THRESHOLD = 10
DM_RATE_WINDOW_SEC = 600

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("sophie-discord")

with CONFIG_PATH.open() as f:
    CONFIG = json.load(f)
ALLOWED_USER_ID = int(CONFIG["allowed_user_id"])
TOKEN = CONFIG["bot_token"]
# Optional: restrict to a specific channel ID (e.g. #sophie in Pendragon & Co).
# If unset, accepts DMs and any channel where the bot is mentioned/posted.
ALLOWED_CHANNEL_ID = int(CONFIG["allowed_channel_id"]) if CONFIG.get("allowed_channel_id") else None
# Optional: ID of Howl's home channel. Required for sophie-task-howl handoffs.
HOWL_CHANNEL_ID = int(CONFIG["howl_channel_id"]) if CONFIG.get("howl_channel_id") else None
TIMEZONE = ZoneInfo(CONFIG.get("timezone", "America/Los_Angeles"))
QUIET_HOURS_DEFAULT = CONFIG.get("quiet_hours") or None  # "HH:MM-HH:MM" or None

# Per-channel state: {channel_id: {"session_id": uuid, "first_call": bool}}
STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
if STATE_PATH.exists():
    STATE = json.loads(STATE_PATH.read_text())
else:
    STATE = {}

# Per-channel locks so concurrent messages are queued, not interleaved.
LOCKS: dict[str, asyncio.Lock] = {}

# Active watchers: {watcher_id: {"task": asyncio.Task, "info": dict}}
WATCHERS: dict[str, dict] = {}

# Persistent scheduled tasks (reminders + autonomy ticks).
# A schedule is a dict with: id, kind ("notify"|"invoke"), fire_at_utc (ISO string),
# recurrence (None | "daily" | "weekly" | "every:Nm" | "every:Nh"),
# message (notify) or prompt (invoke), pause_when_off (bool — gated by !autonomy off),
# defer_if_quiet (bool — shift past quiet hours instead of firing in-window),
# description, created_utc.
if SCHEDULES_PATH.exists():
    SCHEDULES: list[dict] = json.loads(SCHEDULES_PATH.read_text())
else:
    SCHEDULES = []

# Runtime toggles persisted across restarts.
DEFAULT_RUNTIME = {
    "autonomy_enabled": True,
    "quiet_hours_override": None,  # None = use config; "off" = disabled; "HH:MM-HH:MM" = override
    "circuit_breaker_tripped_at": None,
    "circuit_breaker_reason": None,
}
if RUNTIME_PATH.exists():
    RUNTIME = {**DEFAULT_RUNTIME, **json.loads(RUNTIME_PATH.read_text())}
else:
    RUNTIME = dict(DEFAULT_RUNTIME)

# Rolling window of unsolicited DM timestamps for circuit breaker.
DM_LOG: deque[float] = deque()

# Serialize all sophie-sandbox subprocess invocations — two parallel sessions
# would race on the shared /notebook bind mount.
SOPHIE_LOCK = asyncio.Lock()

# In-memory tail of recently sent outbound messages so on_reaction_add can
# resolve a message_id back to its `tag` (e.g. "water") without scanning the
# JSONL log on every tap. Bounded — older entries fall out; reactions on those
# get logged with tag="" and can still be joined later via message_id.
OUTBOUND_TAIL: deque[tuple[int, str]] = deque(maxlen=500)


def _append_jsonl(path: Path, row: dict) -> None:
    """Append a single JSON row + newline. No locking — single writer process."""
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        log.exception(f"failed to append {path.name}")


def _ensure_log_files() -> None:
    """Create the JSONL log files at startup with restrictive perms."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    for p in (INBOUND_LOG, OUTBOUND_LOG, REACTIONS_LOG):
        if not p.exists():
            p.touch(mode=0o600)
        else:
            try:
                os.chmod(p, 0o600)
            except Exception:
                pass


def _resolve_tag(message_id: int) -> str:
    """Look up a Sophie outbound message_id in the in-memory tail; return tag or ''."""
    for mid, tag in reversed(OUTBOUND_TAIL):
        if mid == message_id:
            return tag
    return ""


def _record_outbound(channel_id: int, message_id: int, content: str, tag: str = "") -> None:
    """Persist one outbound row + remember it in the tail cache."""
    _append_jsonl(OUTBOUND_LOG, {
        "ts": now_iso(),
        "channel_id": int(channel_id),
        "message_id": int(message_id),
        "content": content,
        "tag": tag,
    })
    OUTBOUND_TAIL.append((int(message_id), tag))


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _write_last_tick() -> None:
    """Mark 'Sophie was just awake'. Called after every successful run_sophie()."""
    try:
        LAST_TICK_PATH.write_text(json.dumps({"last_tick_utc": now_iso()}))
    except Exception:
        log.exception("failed to write last-tick.json")


_ensure_log_files()


def save_state():
    STATE_PATH.write_text(json.dumps(STATE, indent=2))


def save_schedules():
    SCHEDULES_PATH.write_text(json.dumps(SCHEDULES, indent=2))


def save_runtime():
    RUNTIME_PATH.write_text(json.dumps(RUNTIME, indent=2))


def get_or_create_session(channel_id: str) -> tuple[str, bool]:
    if channel_id not in STATE:
        STATE[channel_id] = {"session_id": str(uuid.uuid4()), "first_call": True}
        save_state()
    entry = STATE[channel_id]
    return entry["session_id"], entry["first_call"]


def mark_session_used(channel_id: str):
    STATE[channel_id]["first_call"] = False
    save_state()


def new_session(channel_id: str) -> str:
    STATE[channel_id] = {"session_id": str(uuid.uuid4()), "first_call": True}
    save_state()
    return STATE[channel_id]["session_id"]


async def run_sophie(session_id: str, first_call: bool, prompt: str) -> str:
    """Run sophie-sandbox -p in a thread (blocking subprocess) and return stdout."""
    if first_call:
        args = [SOPHIE_CMD, "-p", "--session-id", session_id, prompt]
    else:
        args = [SOPHIE_CMD, "-p", "-r", session_id, prompt]

    def _run():
        env = os.environ.copy()
        env["TZ"] = TIMEZONE.key
        try:
            r = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=SOPHIE_TIMEOUT,
                env=env,
            )
            if r.returncode != 0:
                return f"[sophie error rc={r.returncode}]\n{r.stderr.strip() or r.stdout.strip()}"
            return r.stdout.strip() or "(empty response)"
        except subprocess.TimeoutExpired:
            return f"[sophie timed out after {SOPHIE_TIMEOUT}s]"
        except Exception as e:
            return f"[bot error: {e}]"

    async with SOPHIE_LOCK:
        result = await asyncio.to_thread(_run)
    # Mark Sophie as having just been awake — sophie-recent-dms --since-last-tick
    # uses this to know what's new since she last had context.
    _write_last_tick()
    return result


def split_discord(text: str, limit: int = 1900) -> list[str]:
    """Split a long message into Discord-safe chunks, preferring line boundaries."""
    if len(text) <= limit:
        return [text]
    chunks = []
    remaining = text
    while len(remaining) > limit:
        split = remaining.rfind("\n", 0, limit)
        if split == -1:
            split = limit
        chunks.append(remaining[:split])
        remaining = remaining[split:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


# --- Watcher / reminder system ---

async def run_check_command(command: str) -> str:
    """Run a check command inside an ephemeral Sophie sandbox container.

    Mounts /home/sophie/notebook -> /notebook so commands can reference the
    same paths Sophie uses inside her main container.
    """
    args = [
        "docker", "run", "--rm",
        "-v", "/home/sophie/notebook:/notebook",
        "-w", "/notebook",
        "--env-file", "/home/sophie/.claude-container/.env",
        "sophie-sandbox:latest",
        "bash", "-c", command,
    ]
    def _run():
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=120)
            return r.stdout.strip() + r.stderr.strip()
        except subprocess.TimeoutExpired:
            return "[check command timed out]"
        except Exception as e:
            return f"[check error: {e}]"
    return await asyncio.to_thread(_run)


async def send_dm(msg: str, *, tag: str = "", reactions: list[str] | None = None):
    """Send a message to Nick — DM if no allowed channel is configured,
    else post in the allowed channel.

    Each chunk is persisted to outbound-messages.jsonl. If `reactions` are
    provided, they're pre-attached to the LAST chunk so Nick can tap-to-ack
    (e.g. a 💧 next to a "water?" nudge). `tag` is stored on the outbound row
    so reactions can be grouped by topic ("water", "stand", etc.) later.
    """
    if ALLOWED_CHANNEL_ID:
        channel = client.get_channel(ALLOWED_CHANNEL_ID) or await client.fetch_channel(ALLOWED_CHANNEL_ID)
        target = channel
    else:
        target = client.get_user(ALLOWED_USER_ID) or await client.fetch_user(ALLOWED_USER_ID)
    chunks = split_discord(msg)
    sent_messages = []
    for chunk in chunks:
        sent = await target.send(chunk)
        sent_messages.append(sent)
        try:
            _record_outbound(sent.channel.id, sent.id, chunk, tag=tag)
        except Exception:
            log.exception("send_dm: failed to record outbound")
    if reactions and sent_messages:
        last = sent_messages[-1]
        for emoji in reactions:
            try:
                await last.add_reaction(emoji)
            except Exception as e:
                log.warning(f"send_dm: failed to add reaction {emoji!r}: {e}")


async def send_attachment(filename: str, data: bytes, caption: str = ""):
    """Post a file to Nick — same routing as send_dm."""
    file = discord.File(fp=io.BytesIO(data), filename=filename)
    if ALLOWED_CHANNEL_ID:
        target = client.get_channel(ALLOWED_CHANNEL_ID) or await client.fetch_channel(ALLOWED_CHANNEL_ID)
    else:
        target = client.get_user(ALLOWED_USER_ID) or await client.fetch_user(ALLOWED_USER_ID)
    sent = await target.send(content=caption or None, file=file)
    try:
        _record_outbound(sent.channel.id, sent.id, f"[attach:{filename}] {caption}".strip(), tag="attach")
    except Exception:
        log.exception("send_attachment: failed to record outbound")


async def post_to_howl(content: str) -> str:
    """Post a Sophie-authored task into Howl's home channel.

    Returns "ok:<message_id>" on success, "error: ..." on failure.
    Howl's bot is responsible for accepting bot-authored messages from Sophie's
    user ID via its `allowed_bot_user_ids` config.
    """
    if not HOWL_CHANNEL_ID:
        return "error: howl_channel_id not configured"
    try:
        channel = client.get_channel(HOWL_CHANNEL_ID) or await client.fetch_channel(HOWL_CHANNEL_ID)
    except Exception as e:
        return f"error: cannot fetch howl channel: {e}"
    body = f"[From Sophie]: {content}"
    try:
        sent_messages = []
        for chunk in split_discord(body):
            sent = await channel.send(chunk)
            sent_messages.append(sent)
            _record_outbound(sent.channel.id, sent.id, chunk, tag="howl-task")
        return f"ok:{sent_messages[-1].id}"
    except Exception as e:
        log.exception("post_to_howl failed")
        return f"error: send failed: {e}"


async def watcher_loop(watcher_id: str, info: dict):
    """Poll a command at an interval until match or timeout."""
    command = info["command"]
    match = info["match"]
    every = info["every"]
    notify_msg = info["notify"]
    deadline = info["deadline"]
    description = info.get("description", command[:60])

    log.info(f"watcher {watcher_id[:8]} started: every={every}s match={match!r} timeout={int((deadline - time.time()) / 60)}min cmd={command[:80]}")
    try:
        while time.time() < deadline:
            output = await run_check_command(command)
            log.info(f"watcher {watcher_id[:8]} check: {output[:120]}")
            if match.lower() in output.lower():
                await send_dm(f"✅ **Reminder** (`{watcher_id[:8]}`)\n{notify_msg}")
                log.info(f"watcher {watcher_id[:8]} matched, notifying")
                return
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(every, remaining))

        await send_dm(f"⏰ **Reminder expired** (`{watcher_id[:8]}`)\nTimed out: {description}")
        log.info(f"watcher {watcher_id[:8]} timed out")
    except asyncio.CancelledError:
        log.info(f"watcher {watcher_id[:8]} cancelled")
    finally:
        WATCHERS.pop(watcher_id, None)


def start_watcher(payload: dict) -> str:
    """Start a new watcher from a socket payload. Returns status message."""
    command = payload.get("command", "").strip()
    match = payload.get("match", "").strip()
    notify_msg = payload.get("notify", "Reminder condition met.")
    every = max(10, int(payload.get("every", 60)))
    timeout_min = min(MAX_WATCHER_TIMEOUT, max(1, int(payload.get("timeout", DEFAULT_WATCHER_TIMEOUT))))
    description = payload.get("description", command[:60])

    if not command or not match:
        return "error: watch requires 'command' and 'match' fields"

    watcher_id = str(uuid.uuid4())
    deadline = time.time() + timeout_min * 60

    info = {
        "command": command,
        "match": match,
        "notify": notify_msg,
        "every": every,
        "deadline": deadline,
        "timeout_min": timeout_min,
        "description": description,
        "created": time.time(),
    }
    task = asyncio.create_task(watcher_loop(watcher_id, info))
    WATCHERS[watcher_id] = {"task": task, "info": info}

    asyncio.create_task(send_dm(
        f"\U0001f550 **Reminder set** (`{watcher_id[:8]}`)\n"
        f"{description}\n"
        f"Checking every {every}s, expires in {timeout_min}min."
    ))

    return f"ok:{watcher_id}"


def extend_watcher(watcher_id_prefix: str, new_timeout_min: int) -> str:
    """Extend a running watcher's deadline."""
    new_timeout_min = min(MAX_WATCHER_TIMEOUT, max(1, new_timeout_min))
    matches = [wid for wid in WATCHERS if wid.startswith(watcher_id_prefix)]
    if not matches:
        return "error: no watcher found with that ID"
    if len(matches) > 1:
        return f"error: ambiguous ID prefix, matches {len(matches)} watchers"
    wid = matches[0]
    info = WATCHERS[wid]["info"]
    new_deadline = time.time() + new_timeout_min * 60
    if new_deadline > info["created"] + MAX_WATCHER_TIMEOUT * 60:
        new_deadline = info["created"] + MAX_WATCHER_TIMEOUT * 60
    info["deadline"] = new_deadline
    remaining = int((new_deadline - time.time()) / 60)
    return f"ok: extended {wid[:8]} -- {remaining} min remaining (max 24h from creation)"


# --- Scheduler / autonomy / quiet hours ---

def now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def parse_iso_aware(s: str) -> datetime:
    """Parse an ISO 8601 timestamp; naive values are assumed in the user's TZ."""
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TIMEZONE)
    return dt.astimezone(timezone.utc)


def format_local(dt_utc_iso: str) -> str:
    try:
        dt = datetime.fromisoformat(dt_utc_iso).astimezone(TIMEZONE)
        return dt.strftime("%a %b %d %H:%M %Z")
    except Exception:
        return dt_utc_iso


def parse_quiet_hours(s: str | None) -> tuple[int, int, int, int] | None:
    if not s or s.lower() == "off":
        return None
    try:
        a, b = s.split("-")
        sh, sm = a.split(":")
        eh, em = b.split(":")
        return int(sh), int(sm), int(eh), int(em)
    except Exception:
        log.warning(f"could not parse quiet_hours {s!r}")
        return None


def get_quiet_hours() -> tuple[int, int, int, int] | None:
    override = RUNTIME.get("quiet_hours_override")
    if override == "off":
        return None
    if override:
        return parse_quiet_hours(override)
    return parse_quiet_hours(QUIET_HOURS_DEFAULT)


def in_quiet_hours(when_utc: datetime | None = None) -> bool:
    qh = get_quiet_hours()
    if qh is None:
        return False
    when = (when_utc or now_utc()).astimezone(TIMEZONE)
    sh, sm, eh, em = qh
    minutes_now = when.hour * 60 + when.minute
    start = sh * 60 + sm
    end = eh * 60 + em
    if start == end:
        return False
    if start < end:
        return start <= minutes_now < end
    return minutes_now >= start or minutes_now < end


def end_of_quiet_window(when_utc: datetime) -> datetime | None:
    """Given a UTC datetime inside quiet hours, return when the window ends."""
    qh = get_quiet_hours()
    if qh is None:
        return None
    local = when_utc.astimezone(TIMEZONE)
    sh, sm, eh, em = qh
    end_today = local.replace(hour=eh, minute=em, second=0, microsecond=0)
    minutes_now = local.hour * 60 + local.minute
    end_min = eh * 60 + em
    start_min = sh * 60 + sm
    if start_min < end_min:
        if minutes_now < end_min:
            return end_today.astimezone(timezone.utc)
        return None
    if minutes_now >= start_min:
        return (end_today + timedelta(days=1)).astimezone(timezone.utc)
    if minutes_now < end_min:
        return end_today.astimezone(timezone.utc)
    return None


def autonomy_enabled() -> bool:
    return bool(RUNTIME.get("autonomy_enabled", True))


def trip_circuit_breaker(reason: str):
    RUNTIME["autonomy_enabled"] = False
    RUNTIME["circuit_breaker_tripped_at"] = now_utc().isoformat()
    RUNTIME["circuit_breaker_reason"] = reason
    save_runtime()
    log.warning(f"circuit breaker tripped: {reason}")
    asyncio.create_task(send_dm(
        f"⚠️ **Autonomy auto-disabled**\n{reason}\nUse `!autonomy on` once you've reviewed."
    ))


def record_unsolicited_dm():
    """Track unsolicited DM rate. Trips the breaker if over threshold."""
    now_ts = time.time()
    DM_LOG.append(now_ts)
    cutoff = now_ts - DM_RATE_WINDOW_SEC
    while DM_LOG and DM_LOG[0] < cutoff:
        DM_LOG.popleft()
    if autonomy_enabled() and len(DM_LOG) > DM_RATE_THRESHOLD:
        trip_circuit_breaker(
            f"More than {DM_RATE_THRESHOLD} unsolicited messages in "
            f"{DM_RATE_WINDOW_SEC // 60} minutes."
        )


def compute_next_fire(s: dict) -> datetime | None:
    rec = s.get("recurrence")
    if not rec:
        return None
    fired_at = parse_iso_aware(s["fire_at_utc"])
    if rec == "daily":
        return fired_at + timedelta(days=1)
    if rec == "weekly":
        return fired_at + timedelta(days=7)
    if rec.startswith("every:"):
        spec = rec[len("every:"):]
        unit = spec[-1]
        try:
            n = int(spec[:-1])
        except ValueError:
            log.warning(f"bad recurrence spec {rec!r}")
            return None
        if unit == "m":
            return fired_at + timedelta(minutes=n)
        if unit == "h":
            return fired_at + timedelta(hours=n)
        if unit == "d":
            return fired_at + timedelta(days=n)
    log.warning(f"unknown recurrence {rec!r}")
    return None


async def fire_schedule(s: dict):
    sid = s["id"]
    desc = s.get("description", "")[:60]
    log.info(f"schedule {sid[:8]} firing: kind={s['kind']} desc={desc!r}")
    try:
        if s["kind"] == "notify":
            msg = s.get("message", "(no message)")
            await send_dm(f"⏰ **Reminder**\n{msg}")
            record_unsolicited_dm()
        elif s["kind"] == "invoke":
            prompt = s.get("prompt", "")
            session_id = s.get("session_id") or str(uuid.uuid4())
            reply = await run_sophie(session_id, True, prompt)
            log.info(f"schedule {sid[:8]} invoke stdout: {reply[:200]}")
            # Sophie messages Nick herself via sophie-notify; only surface failures.
            if reply.startswith(("[sophie error", "[sophie timed out", "[bot error")):
                await send_dm(f"⚠️ Scheduled invoke `{sid[:8]}` ({desc}) failed:\n```\n{reply[:500]}\n```")
                record_unsolicited_dm()
        else:
            log.warning(f"schedule {sid[:8]} unknown kind {s['kind']!r}")
    except Exception as e:
        log.exception(f"schedule {sid[:8]} fire error: {e}")
        try:
            await send_dm(f"⚠️ Scheduled task `{sid[:8]}` ({desc}) errored: {e}")
        except Exception:
            pass


async def scheduler_tick():
    now = now_utc()
    changed = False
    remaining: list[dict] = []
    for s in SCHEDULES:
        try:
            fire_at = parse_iso_aware(s["fire_at_utc"])
        except Exception as e:
            log.warning(f"schedule {s.get('id','?')[:8]} bad fire_at: {e}; dropping")
            changed = True
            continue

        if fire_at > now:
            remaining.append(s)
            continue

        should_skip = False
        deferred_until: datetime | None = None

        if s.get("pause_when_off") and not autonomy_enabled():
            log.info(f"schedule {s['id'][:8]} skipped (autonomy off)")
            should_skip = True
        elif in_quiet_hours(now):
            if s.get("pause_when_off"):
                log.info(f"schedule {s['id'][:8]} skipped (quiet hours)")
                should_skip = True
            elif s.get("defer_if_quiet"):
                deferred_until = end_of_quiet_window(now)
                log.info(f"schedule {s['id'][:8]} deferred to {deferred_until}")

        if deferred_until:
            s["fire_at_utc"] = deferred_until.isoformat()
            remaining.append(s)
            changed = True
            continue

        if not should_skip:
            asyncio.create_task(fire_schedule(s))
            changed = True

        next_fire = compute_next_fire(s)
        if next_fire:
            # advance until strictly in the future to avoid rapid re-fires
            while next_fire <= now:
                s["fire_at_utc"] = next_fire.isoformat()
                bumped = compute_next_fire(s)
                next_fire = bumped if bumped else (next_fire + timedelta(days=1))
            s["fire_at_utc"] = next_fire.isoformat()
            remaining.append(s)

    if changed:
        SCHEDULES[:] = remaining
        save_schedules()


async def scheduler_loop():
    log.info(
        f"scheduler running (tick={SCHEDULER_TICK_SEC}s tz={TIMEZONE.key} "
        f"quiet={QUIET_HOURS_DEFAULT or 'off'} autonomy={'on' if autonomy_enabled() else 'off'})"
    )
    while True:
        try:
            await scheduler_tick()
        except Exception:
            log.exception("scheduler_tick errored")
        await asyncio.sleep(SCHEDULER_TICK_SEC)


def run_missed_fires_on_startup():
    """Recover from downtime. Within MISSED_FIRE_GRACE_SEC the next tick fires as usual.
    Older misses surface as a 'missed' notice (autonomy ticks roll forward silently).
    Recurring schedules always advance to a future fire time."""
    now = now_utc()
    changed = False
    remaining: list[dict] = []
    missed_notes: list[str] = []
    for s in SCHEDULES:
        try:
            fire_at = parse_iso_aware(s["fire_at_utc"])
        except Exception:
            log.warning(f"dropping schedule {s.get('id','?')[:8]} with bad fire_at")
            changed = True
            continue
        if fire_at > now:
            remaining.append(s)
            continue
        late_by = (now - fire_at).total_seconds()
        if late_by <= MISSED_FIRE_GRACE_SEC:
            remaining.append(s)
            continue
        if not s.get("pause_when_off"):
            desc = s.get("description") or s.get("message") or s.get("prompt", "")[:60]
            missed_notes.append(f"`{s['id'][:8]}` ({desc[:80]}) — was due {format_local(s['fire_at_utc'])}")
        next_fire = compute_next_fire(s)
        if next_fire:
            while next_fire <= now:
                s["fire_at_utc"] = next_fire.isoformat()
                bumped = compute_next_fire(s)
                next_fire = bumped if bumped else (next_fire + timedelta(days=1))
            s["fire_at_utc"] = next_fire.isoformat()
            remaining.append(s)
        changed = True

    if changed:
        SCHEDULES[:] = remaining
        save_schedules()

    if missed_notes:
        body = "⚠️ **Missed reminders while I was down:**\n" + "\n".join(missed_notes)
        asyncio.create_task(send_dm(body))


# --- Schedule add/list/cancel (called from socket and Discord) ---

def _validate_recurrence(rec: str | None) -> str | None:
    if not rec:
        return None
    if rec in ("daily", "weekly"):
        return rec
    if rec.startswith("every:"):
        spec = rec[len("every:"):]
        if len(spec) >= 2 and spec[-1] in "mhd":
            try:
                int(spec[:-1])
                return rec
            except ValueError:
                pass
    raise ValueError(f"recurrence must be 'daily', 'weekly', or 'every:Nm|Nh|Nd' (got {rec!r})")


def schedule_add(payload: dict) -> str:
    kind = payload.get("kind")
    if kind not in ("notify", "invoke"):
        return "error: kind must be 'notify' or 'invoke'"
    fire_at_raw = (payload.get("fire_at") or "").strip()
    if not fire_at_raw:
        return "error: fire_at required"
    try:
        fire_at = parse_iso_aware(fire_at_raw)
    except ValueError as e:
        return f"error: {e}"
    if fire_at < now_utc() - timedelta(seconds=60):
        return f"error: fire_at is in the past ({format_local(fire_at.isoformat())})"
    if fire_at > now_utc() + timedelta(days=MAX_SCHEDULE_HORIZON_DAYS):
        return f"error: fire_at more than {MAX_SCHEDULE_HORIZON_DAYS}d out"
    if kind == "notify" and not (payload.get("message") or "").strip():
        return "error: notify schedule requires non-empty message"
    if kind == "invoke" and not (payload.get("prompt") or "").strip():
        return "error: invoke schedule requires non-empty prompt"
    try:
        rec = _validate_recurrence(payload.get("recurrence"))
    except ValueError as e:
        return f"error: {e}"
    sid = str(uuid.uuid4())
    desc_default = payload.get("message") or payload.get("prompt", "")
    entry = {
        "id": sid,
        "kind": kind,
        "fire_at_utc": fire_at.isoformat(),
        "recurrence": rec,
        "message": payload.get("message", ""),
        "prompt": payload.get("prompt", ""),
        "session_id": payload.get("session_id") or None,
        "pause_when_off": bool(payload.get("pause_when_off", False)),
        "defer_if_quiet": bool(payload.get("defer_if_quiet", False)),
        "description": (payload.get("description") or desc_default)[:120],
        "created_utc": now_utc().isoformat(),
    }
    SCHEDULES.append(entry)
    save_schedules()
    log.info(f"schedule {sid[:8]} added: kind={kind} at={entry['fire_at_utc']} rec={rec}")
    return f"ok:{sid}"


def schedule_list_text() -> str:
    if not SCHEDULES:
        return "(no scheduled tasks)"
    lines = []
    for s in sorted(SCHEDULES, key=lambda x: x["fire_at_utc"]):
        flags = []
        if s.get("pause_when_off"):
            flags.append("autonomy")
        if s.get("defer_if_quiet"):
            flags.append("defer-if-quiet")
        if s.get("recurrence"):
            flags.append(s["recurrence"])
        flag_str = f" [{','.join(flags)}]" if flags else ""
        lines.append(
            f"{s['id'][:8]} | {s['kind']:6s} | {format_local(s['fire_at_utc'])}{flag_str} | {s.get('description','')[:60]}"
        )
    return "\n".join(lines)


def schedule_cancel(id_prefix: str) -> str:
    matches = [s for s in SCHEDULES if s["id"].startswith(id_prefix)]
    if not matches:
        return "error: no schedule found"
    if len(matches) > 1:
        return f"error: ambiguous prefix, matches {len(matches)}"
    SCHEDULES.remove(matches[0])
    save_schedules()
    return f"ok: cancelled {matches[0]['id'][:8]}"


def cancel_all_autonomy_schedules() -> int:
    before = len(SCHEDULES)
    kept = [s for s in SCHEDULES if not s.get("pause_when_off")]
    removed = before - len(kept)
    if removed:
        SCHEDULES[:] = kept
        save_schedules()
    return removed


# --- Socket listener ---

async def notify_listener():
    """Listen on a Unix socket for notifications and watcher commands."""
    try:
        os.unlink(NOTIFY_SOCKET)
    except FileNotFoundError:
        pass

    async def handle(reader, writer):
        try:
            # Read until EOF — the client does SHUT_WR after sending the full payload.
            # A bounded read() truncates large payloads (e.g. base64-encoded image attachments).
            data = await reader.read()
            if not data:
                return
            text = data.decode("utf-8", errors="replace").strip()

            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = None

            if payload and isinstance(payload, dict):
                ptype = payload.get("type", "notify")

                if ptype == "watch":
                    result = start_watcher(payload)
                    log.info(f"watch request -> {result[:80]}")
                    try:
                        writer.write(f"{result}\n".encode())
                        await writer.drain()
                    except (ConnectionResetError, BrokenPipeError):
                        pass
                    return

                if ptype == "extend":
                    wid = payload.get("id", "")
                    timeout = int(payload.get("timeout", 60))
                    result = extend_watcher(wid, timeout)
                    log.info(f"extend request -> {result[:80]}")
                    try:
                        writer.write(f"{result}\n".encode())
                        await writer.drain()
                    except (ConnectionResetError, BrokenPipeError):
                        pass
                    return

                if ptype == "list":
                    lines = []
                    for wid, w in WATCHERS.items():
                        info = w["info"]
                        remaining = max(0, int((info["deadline"] - time.time()) / 60))
                        lines.append(f"{wid[:8]} | every {info['every']}s | {remaining}min left | {info['description'][:50]}")
                    result = "\n".join(lines) if lines else "(no active reminders)"
                    try:
                        writer.write(f"{result}\n".encode())
                        await writer.drain()
                    except (ConnectionResetError, BrokenPipeError):
                        pass
                    return

                if ptype == "cancel":
                    wid_prefix = payload.get("id", "")
                    matches = [wid for wid in WATCHERS if wid.startswith(wid_prefix)]
                    if not matches:
                        result = "error: no watcher found"
                    elif len(matches) > 1:
                        result = f"error: ambiguous, matches {len(matches)}"
                    else:
                        WATCHERS[matches[0]]["task"].cancel()
                        result = f"ok: cancelled {matches[0][:8]}"
                    try:
                        writer.write(f"{result}\n".encode())
                        await writer.drain()
                    except (ConnectionResetError, BrokenPipeError):
                        pass
                    return

                if ptype == "attach":
                    fname = payload.get("filename", "attachment.bin")
                    caption = payload.get("caption", "")
                    try:
                        data = base64.b64decode(payload.get("data_b64", ""))
                    except Exception as e:
                        result = f"error: bad base64 payload: {e}"
                    else:
                        try:
                            await send_attachment(fname, data, caption=caption)
                            result = f"ok: posted {fname} ({len(data)} bytes)"
                            log.info(f"attach -> user: {fname} ({len(data)} bytes)")
                            record_unsolicited_dm()
                        except Exception as e:
                            result = f"error: send failed: {e}"
                            log.exception("attach send failed")
                    try:
                        writer.write(f"{result}\n".encode())
                        await writer.drain()
                    except (ConnectionResetError, BrokenPipeError):
                        pass
                    return

                if ptype == "schedule_add":
                    result = schedule_add(payload)
                    log.info(f"schedule_add request -> {result[:80]}")
                    try:
                        writer.write(f"{result}\n".encode())
                        await writer.drain()
                    except (ConnectionResetError, BrokenPipeError):
                        pass
                    return

                if ptype == "schedule_list":
                    result = schedule_list_text()
                    try:
                        writer.write(f"{result}\n".encode())
                        await writer.drain()
                    except (ConnectionResetError, BrokenPipeError):
                        pass
                    return

                if ptype == "schedule_cancel":
                    result = schedule_cancel(payload.get("id", ""))
                    log.info(f"schedule_cancel request -> {result[:80]}")
                    try:
                        writer.write(f"{result}\n".encode())
                        await writer.drain()
                    except (ConnectionResetError, BrokenPipeError):
                        pass
                    return

                if ptype == "post_to_howl":
                    content = (payload.get("content") or payload.get("message") or "").strip()
                    if not content:
                        result = "error: post_to_howl requires non-empty content"
                    else:
                        result = await post_to_howl(content)
                    log.info(f"post_to_howl -> {result[:80]}")
                    try:
                        writer.write(f"{result}\n".encode())
                        await writer.drain()
                    except (ConnectionResetError, BrokenPipeError):
                        pass
                    return

                # Default: treat as notification (with optional --track reaction support)
                msg = payload.get("message", "")
                tag = (payload.get("track") or "").strip()
                reactions_field = payload.get("reactions")
                if isinstance(reactions_field, str):
                    reactions_list = [e.strip() for e in reactions_field.split(",") if e.strip()]
                elif isinstance(reactions_field, list):
                    reactions_list = [str(e) for e in reactions_field if str(e).strip()]
                else:
                    reactions_list = []
            else:
                msg = text
                tag = ""
                reactions_list = []

            if msg:
                await send_dm(msg, tag=tag, reactions=reactions_list or None)
                log.info(f"notify -> user: {msg[:80]} tag={tag or '-'} reactions={reactions_list or '-'}")
                record_unsolicited_dm()
            try:
                writer.write(b"ok\n")
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                pass
        except Exception as e:
            log.exception(f"socket handler error: {e}")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_unix_server(handle, path=NOTIFY_SOCKET)
    os.chmod(NOTIFY_SOCKET, 0o666)
    log.info(f"socket listener on {NOTIFY_SOCKET}")
    async with server:
        await server.serve_forever()


intents = discord.Intents.default()
intents.message_content = True
intents.dm_messages = True
intents.guild_messages = True
client = discord.Client(intents=intents)


@client.event
async def on_ready():
    log.info(f"Logged in as {client.user} (id={client.user.id})")
    log.info(f"Allowed user id: {ALLOWED_USER_ID}")
    if ALLOWED_CHANNEL_ID:
        log.info(f"Allowed channel id: {ALLOWED_CHANNEL_ID}")
    if not hasattr(client, "_socket_task"):
        client._socket_task = asyncio.create_task(notify_listener())
    if not hasattr(client, "_scheduler_task"):
        run_missed_fires_on_startup()
        client._scheduler_task = asyncio.create_task(scheduler_loop())


def message_is_from_nick_in_allowed_place(message: discord.Message) -> bool:
    """Sophie listens to:
    - DMs from the allowed user (always)
    - The configured `home` channel (if set), from the allowed user
    - Any channel where she's @mentioned by the allowed user
    """
    if message.author.bot or message.author.id != ALLOWED_USER_ID:
        return False
    if isinstance(message.channel, discord.DMChannel):
        return True
    if ALLOWED_CHANNEL_ID and message.channel.id == ALLOWED_CHANNEL_ID:
        return True
    if client.user in message.mentions:
        return True
    return False


def strip_bot_mention(content: str) -> str:
    """Remove `<@id>` and `<@!id>` (nickname) mentions of the bot from a message."""
    return (
        content
        .replace(f"<@{client.user.id}>", "")
        .replace(f"<@!{client.user.id}>", "")
        .strip()
    )


@client.event
async def on_message(message: discord.Message):
    if not message_is_from_nick_in_allowed_place(message):
        return

    channel_id = str(message.channel.id)
    content = strip_bot_mention(message.content).strip()
    if not content:
        return

    # Persist before forwarding — even bot commands like !panic count as
    # context worth seeing later via sophie-recent-dms.
    _append_jsonl(INBOUND_LOG, {
        "ts": now_iso(),
        "channel_id": int(message.channel.id),
        "message_id": int(message.id),
        "author_id": int(message.author.id),
        "content": content,
    })

    # --- Commands ---
    if content == "!new":
        sid = new_session(channel_id)
        await message.channel.send(f"\U0001f195 New session started: `{sid[:8]}`")
        return

    if content == "!status":
        entry = STATE.get(channel_id)
        if not entry:
            await message.channel.send("No active session. Send a message to start one.")
        else:
            sid = entry["session_id"]
            state = "not yet used" if entry["first_call"] else "active"
            await message.channel.send(f"Session: `{sid[:8]}`\nState: {state}")
        return

    if content == "!logs":
        try:
            lines = TRIPWIRE_LOG.read_text().strip().splitlines()[-20:]
            body = "\n".join(lines) if lines else "(empty)"
            await message.channel.send(f"```\n{body[:1800]}\n```")
        except Exception as e:
            await message.channel.send(f"log read error: {e}")
        return

    if content == "!reminders":
        if not WATCHERS:
            await message.channel.send("No active reminders.")
        else:
            lines = []
            for wid, w in WATCHERS.items():
                info = w["info"]
                remaining = max(0, int((info["deadline"] - time.time()) / 60))
                lines.append(f"`{wid[:8]}` | every {info['every']}s | {remaining}min left | {info['description'][:50]}")
            await message.channel.send("\n".join(lines))
        return

    if content.startswith("!cancel "):
        wid_prefix = content.split(None, 1)[1].strip()
        matches = [wid for wid in WATCHERS if wid.startswith(wid_prefix)]
        if not matches:
            await message.channel.send("No reminder found with that ID.")
        elif len(matches) > 1:
            await message.channel.send(f"Ambiguous ID -- matches {len(matches)} reminders.")
        else:
            WATCHERS[matches[0]]["task"].cancel()
            await message.channel.send(f"Cancelled reminder `{matches[0][:8]}`")
        return

    # --- Autonomy / quiet-hours / scheduler controls ---
    if content == "!autonomy" or content.startswith("!autonomy "):
        parts = content.split()
        sub = parts[1].lower() if len(parts) > 1 else "status"
        if sub in ("on", "off"):
            RUNTIME["autonomy_enabled"] = (sub == "on")
            if sub == "on":
                RUNTIME["circuit_breaker_tripped_at"] = None
                RUNTIME["circuit_breaker_reason"] = None
            save_runtime()
            log.info(f"!autonomy {sub} (now: {autonomy_enabled()})")
            await message.channel.send(f"Autonomy is now **{sub}**.")
        elif sub == "status":
            qh = get_quiet_hours()
            qh_str = f"{qh[0]:02d}:{qh[1]:02d}-{qh[2]:02d}:{qh[3]:02d}" if qh else "off"
            tripped = RUNTIME.get("circuit_breaker_reason")
            tripped_str = f"\nLast trip: {tripped}" if tripped else ""
            await message.channel.send(
                f"Autonomy: **{'on' if autonomy_enabled() else 'off'}**\n"
                f"Quiet hours: {qh_str} ({TIMEZONE.key})"
                f"{tripped_str}"
            )
        else:
            await message.channel.send("Usage: `!autonomy [on|off|status]`")
        return

    if content == "!quiet" or content.startswith("!quiet "):
        parts = content.split()
        if len(parts) == 1:
            qh = get_quiet_hours()
            qh_str = f"{qh[0]:02d}:{qh[1]:02d}-{qh[2]:02d}:{qh[3]:02d}" if qh else "off"
            in_q = "yes" if in_quiet_hours() else "no"
            await message.channel.send(f"Quiet hours: **{qh_str}** ({TIMEZONE.key}). In window now: {in_q}.")
        else:
            arg = parts[1].lower()
            if arg == "off":
                RUNTIME["quiet_hours_override"] = "off"
                save_runtime()
                await message.channel.send("Quiet hours disabled (override).")
            elif arg == "default":
                RUNTIME["quiet_hours_override"] = None
                save_runtime()
                await message.channel.send(f"Quiet hours reverted to config default ({QUIET_HOURS_DEFAULT or 'off'}).")
            elif parse_quiet_hours(arg):
                RUNTIME["quiet_hours_override"] = arg
                save_runtime()
                log.info(f"!quiet override set to {arg}")
                await message.channel.send(f"Quiet hours set to **{arg}**.")
            else:
                await message.channel.send("Usage: `!quiet [HH:MM-HH:MM | off | default]`")
        return

    if content == "!schedules":
        body = schedule_list_text()
        if len(body) > 1800:
            body = body[:1800] + "\n…(truncated)"
        await message.channel.send(f"```\n{body}\n```")
        return

    if content.startswith("!schedule cancel "):
        sid_prefix = content[len("!schedule cancel "):].strip()
        result = schedule_cancel(sid_prefix)
        await message.channel.send(result)
        return

    if content == "!panic":
        RUNTIME["autonomy_enabled"] = False
        RUNTIME["circuit_breaker_tripped_at"] = now_utc().isoformat()
        RUNTIME["circuit_breaker_reason"] = "panic command"
        save_runtime()
        removed = cancel_all_autonomy_schedules()
        log.warning(f"!panic invoked — autonomy off, {removed} autonomous schedule(s) cleared")
        await message.channel.send(
            f"\U0001f6d1 **Panic.** Autonomy off. {removed} autonomous schedule(s) cleared. "
            f"Explicit reminders kept."
        )
        return

    # --- Help ---
    if content == "!help":
        await message.channel.send(
            "**Commands:**\n"
            "`!new` — start a fresh session with Sophie\n"
            "`!status` — show current session info\n"
            "`!logs` — show last 20 tripwire log lines\n"
            "`!reminders` — list active watchers (poll-based)\n"
            "`!cancel <id>` — cancel a watcher\n"
            "`!schedules` — list scheduled reminders / autonomy ticks\n"
            "`!schedule cancel <id>` — cancel a schedule\n"
            "`!autonomy [on|off|status]` — toggle Sophie's autonomous wakeups\n"
            "`!quiet [HH:MM-HH:MM|off|default]` — set/show quiet hours window\n"
            "`!panic` — disable autonomy + clear all autonomous schedules\n"
            "`!help` — this message\n"
            "\nAnything else is forwarded to Sophie."
        )
        return

    # --- Forward to Sophie ---
    lock = LOCKS.setdefault(channel_id, asyncio.Lock())
    async with lock:
        session_id, first_call = get_or_create_session(channel_id)
        log.info(f"forwarding (channel={channel_id} session={session_id[:8]} first={first_call}): {content[:80]}")
        try:
            await message.add_reaction("⏳")
        except Exception:
            pass
        try:
            async with message.channel.typing():
                reply = await run_sophie(session_id, first_call, content)
        finally:
            try:
                await message.remove_reaction("⏳", client.user)
            except Exception:
                pass
        if first_call:
            mark_session_used(channel_id)
        try:
            await message.add_reaction("✅")
        except Exception:
            pass
        for chunk in split_discord(reply):
            await message.channel.send(chunk)


@client.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    """Persist reactions Nick adds to Sophie's messages so habit nudges close
    the loop. Uses raw event so reactions on uncached messages still fire.
    """
    if payload.user_id != ALLOWED_USER_ID:
        return
    try:
        emoji = str(payload.emoji)
        message_id = int(payload.message_id)
        tag = _resolve_tag(message_id)
        _append_jsonl(REACTIONS_LOG, {
            "ts": now_iso(),
            "message_id": message_id,
            "channel_id": int(payload.channel_id),
            "emoji": emoji,
            "tag": tag,
        })
        log.info(f"reaction logged: {emoji} on {message_id} (tag={tag or '-'})")
    except Exception:
        log.exception("on_raw_reaction_add: failed to log reaction")


client.run(TOKEN, log_handler=None)
