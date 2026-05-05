#!/usr/bin/env python3
"""Discord <> Claude Code bridge. Forwards DMs from an allowed user to claude-sandbox -p."""
import asyncio
import json
import logging
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

import discord

CONFIG_PATH = Path("/etc/claude-discord/config.json")
STATE_PATH = Path("/var/lib/claude-discord/sessions.json")
TRIPWIRE_LOG = Path("/home/claude/.claude-container/tripwire.log")
CLAUDE_CMD = "/usr/local/bin/claude-sandbox"
NOTIFY_SOCKET = "/run/claude-discord.sock"
CLAUDE_TIMEOUT = None  # no timeout
MAX_WATCHER_TIMEOUT = 1440  # 24 hours in minutes
DEFAULT_WATCHER_TIMEOUT = 60  # 1 hour in minutes
MAX_AUTO_DURATION = 1440  # 24 hours in minutes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("claude-discord")

with CONFIG_PATH.open() as f:
    CONFIG = json.load(f)
ALLOWED_USER_ID = int(CONFIG["allowed_user_id"])
TOKEN = CONFIG["bot_token"]
# Optional: a "home" channel ID. If set, Howl responds to every message Nick posts
# there. He can also @mention Howl in any channel to summon him. DMs always work.
ALLOWED_CHANNEL_ID = int(CONFIG["allowed_channel_id"]) if CONFIG.get("allowed_channel_id") else None

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

# Active auto-loops: {project: {"task": asyncio.Task, "info": dict}}
AUTO_LOOPS: dict[str, dict] = {}


def save_state():
    STATE_PATH.write_text(json.dumps(STATE, indent=2))


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


async def run_claude(session_id: str, first_call: bool, prompt: str) -> str:
    """Run claude-sandbox -p in a thread (blocking subprocess) and return stdout."""
    if first_call:
        args = [CLAUDE_CMD, "-p", "--session-id", session_id, prompt]
    else:
        args = [CLAUDE_CMD, "-p", "-r", session_id, prompt]

    def _run():
        try:
            r = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=CLAUDE_TIMEOUT,
            )
            if r.returncode != 0:
                return f"[claude error rc={r.returncode}]\n{r.stderr.strip() or r.stdout.strip()}"
            return r.stdout.strip() or "(empty response)"
        except subprocess.TimeoutExpired:
            return f"[claude timed out after {CLAUDE_TIMEOUT}s]"
        except Exception as e:
            return f"[bot error: {e}]"

    return await asyncio.to_thread(_run)


def split_discord(text: str, limit: int = 1900) -> list[str]:
    """Split a long message into Discord-safe chunks, preferring code-block boundaries."""
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


# --- Watcher system ---

async def run_check_command(command: str) -> str:
    """Run a check command inside an ephemeral sandbox container.

    Mounts /home/claude/projects -> /workspace so commands can `cd /workspace/<project>`
    the same way Claude does inside its main sandbox container.
    """
    args = [
        "docker", "run", "--rm",
        "-v", "/home/claude/projects:/workspace",
        "-w", "/workspace",
        "--env-file", "/home/claude/.claude-container/.env",
        "claude-sandbox:latest",
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


async def send_dm(msg: str):
    """Send a DM to the allowed user."""
    user = client.get_user(ALLOWED_USER_ID) or await client.fetch_user(ALLOWED_USER_ID)
    for chunk in split_discord(msg):
        await user.send(chunk)


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
                await send_dm(f"\u2705 **Watcher triggered** (`{watcher_id[:8]}`)\n{notify_msg}")
                log.info(f"watcher {watcher_id[:8]} matched, notifying")
                return
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(every, remaining))

        await send_dm(f"\u23f0 **Watcher expired** (`{watcher_id[:8]}`)\nTimed out watching: {description}")
        log.info(f"watcher {watcher_id[:8]} timed out")
    except asyncio.CancelledError:
        log.info(f"watcher {watcher_id[:8]} cancelled")
    finally:
        WATCHERS.pop(watcher_id, None)


def start_watcher(payload: dict) -> str:
    """Start a new watcher from a socket payload. Returns status message."""
    command = payload.get("command", "").strip()
    match = payload.get("match", "").strip()
    notify_msg = payload.get("notify", "Watcher condition met.")
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
        f"\U0001f440 **Watcher started** (`{watcher_id[:8]}`)\n"
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


# --- Auto loop system ---

def parse_duration(s: str) -> int:
    """Parse duration string like '4h', '30m', '2h30m' into minutes."""
    total = 0
    m = re.findall(r"(\d+)\s*(h|m)", s.lower())
    for val, unit in m:
        if unit == "h":
            total += int(val) * 60
        elif unit == "m":
            total += int(val)
    if not m:
        # Try plain number as minutes
        try:
            total = int(s)
        except ValueError:
            return 0
    return total


async def gather_project_state(project: str, repo: str) -> str:
    """Gather current PR and issue state for the autonomous prompt."""
    pr_cmd = f"cd /workspace/{project} && gh pr list --json number,title,state,statusCheckRollup,mergeable --limit 20 2>/dev/null || echo '[]'"
    issue_cmd = f"gh issue list -R nickolu/{repo} --label backlog --json number,title,labels --limit 20 2>/dev/null; gh issue list -R nickolu/{repo} --json number,title,labels --limit 20 2>/dev/null"

    pr_out, issue_out = await asyncio.gather(
        run_check_command(pr_cmd),
        run_check_command(issue_cmd),
    )
    return f"OPEN PRs:\n{pr_out}\n\nOPEN ISSUES:\n{issue_out}"


def build_auto_prompt(project: str, repo: str, instructions: str, state: str, cycle: int) -> str:
    """Build the autonomous cycle prompt."""
    return f"""You are in autonomous mode for project `{project}` (repo: nickolu/{repo}).
Working directory: /workspace/{project}

Cycle #{cycle}. User instructions: {instructions or 'none specified'}

{state}

Execute ONE cycle of this priority loop:

1. **Merge ready PRs.** Check open PRs above. If any have passing CI and no conflicts, merge them with `gh pr merge`. Skip PRs where CI is still running (set up a claude-watch for those instead).
2. **Fix failing PRs.** If any PRs have failing CI or merge conflicts, check out the branch, diagnose, fix, and push. Use `claude-watch` to monitor CI after pushing.
3. **Work on next task.** If no PRs need attention, look at open issues (labeled 'backlog' first, then any open issues). Pick the highest priority one. Implement it, create a PR, set up a `claude-watch` for CI.
4. **Brainstorm if idle.** If there are no issues to work on, brainstorm 3-5 improvements that align with the user's instructions. Create GitHub issues for them (label: backlog). Then pick the best one and start working on it.

Rules:
- Only do ONE major unit of work per cycle (one PR fix, one feature, one brainstorm+start). Don't try to do everything.
- Use `claude-notify` for meaningful status updates.
- After creating a PR, set up `claude-watch` to monitor CI status.
- Do not merge PRs without CI passing.
- Use subagents for scouting and execution (advisor pattern).
- End your response with a brief summary of what you did this cycle.

After the summary, the VERY LAST LINE of your response must be exactly one of:
- `STATUS: ACTIVE` — you merged a PR, fixed/pushed commits to a PR, opened a new PR, or otherwise produced commits/PRs/merges this cycle. The loop will immediately start the next cycle.
- `STATUS: IDLE` — you only inspected state, found nothing actionable, or only brainstormed without opening any PR or issue. The loop will sleep the interval before the next cycle.
- `STATUS: STOP` — you have completed the user's stated objective and there is no more work that fits the instructions. Use this only when the instructions were bounded (e.g., "finish issue #529 then stop", "work on tap tap then stop"). Do NOT use STOP for transient blocks or unfinished work — use IDLE for those. Put a one-line `Reason: <why>` immediately above the STATUS line.

If unsure, emit `STATUS: IDLE` (safer default — better to wait than to spin or stop early).
"""


STATUS_RE = re.compile(r"^\s*STATUS:\s*(ACTIVE|IDLE|STOP)\s*$", re.IGNORECASE | re.MULTILINE)
STOP_REASON_RE = re.compile(r"^\s*Reason:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def parse_cycle_status(reply: str) -> tuple[str, str]:
    """Return (status, reason). Status is 'ACTIVE'|'IDLE'|'STOP'.

    Defaults to ('IDLE', '') if no marker is found. `reason` is only set for STOP.
    """
    matches = STATUS_RE.findall(reply)
    status = matches[-1].upper() if matches else "IDLE"
    reason = ""
    if status == "STOP":
        r = STOP_REASON_RE.findall(reply)
        if r:
            reason = r[-1].strip()
    return status, reason


async def auto_loop(project: str, info: dict):
    """Run the autonomous development loop.

    Reads `instructions` and `cycle` live from `info` so they can be mutated
    mid-run (e.g. via `!auto update`). Cycles chain back-to-back on STATUS: ACTIVE,
    sleep `interval_min` on STATUS: IDLE / missing marker / error, and exit cleanly
    on STATUS: STOP.
    """
    session_id = str(uuid.uuid4())
    repo = info["repo"]
    deadline = info["deadline"]
    duration_min = info["duration_min"]
    interval_min = info["interval_min"]
    last_announced_instructions = info["instructions"]

    log.info(f"auto-loop started: project={project} repo={repo} duration={duration_min}m interval={interval_min}m")
    await send_dm(
        f"\U0001f916 **Auto mode started** for `{project}`\n"
        f"Duration: {duration_min}min | Idle interval: {interval_min}min\n"
        f"Instructions: {info['instructions'] or '(none)'}\n"
        f"Session: `{session_id[:8]}`\n"
        f"Use `!auto stop` to end early, or `!auto update <new instructions>` to steer."
    )

    try:
        while time.time() < deadline:
            info["cycle"] += 1
            cycle = info["cycle"]
            instructions = info["instructions"]
            remaining = int((deadline - time.time()) / 60)
            log.info(f"auto-loop {project} cycle {cycle} ({remaining}min remaining)")

            cycle_dm = f"\U0001f680 **Auto cycle #{cycle} starting** — `{project}` ({remaining}min left)"
            if instructions != last_announced_instructions:
                cycle_dm += f"\n\U0001f4dd New instructions in effect: {instructions or '(none)'}"
                last_announced_instructions = instructions
            await send_dm(cycle_dm)

            status = "IDLE"
            stop_reason = ""
            reply = ""
            try:
                state = await gather_project_state(project, repo)
                prompt = build_auto_prompt(project, repo, instructions, state, cycle)
                first_call = (cycle == 1)
                reply = await run_claude(session_id, first_call, prompt)
                status, stop_reason = parse_cycle_status(reply)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception(f"auto-loop {project} cycle {cycle} failed")
                await send_dm(
                    f"⚠️ **Auto cycle #{cycle} errored** — `{project}`: {e}\n"
                    f"Backing off {interval_min}min."
                )

            if reply:
                summary = reply[-1500:] if len(reply) > 1500 else reply
                if status == "ACTIVE":
                    mode_label = "chaining next cycle"
                elif status == "STOP":
                    mode_label = "self-stopping"
                else:
                    mode_label = f"idle — sleeping {interval_min}min"
                await send_dm(
                    f"\U0001f504 **Auto cycle #{cycle}** — `{project}` "
                    f"({remaining}min left, {mode_label})\n{summary}"
                )

            log.info(f"auto-loop {project} cycle {cycle} status={status}")

            if status == "STOP":
                tail = f" — {stop_reason}" if stop_reason else "."
                await send_dm(
                    f"\U0001f6d1 **Auto mode self-stopped** for `{project}` after {cycle} cycles{tail}"
                )
                log.info(f"auto-loop {project} self-stopped after {cycle} cycles: {stop_reason}")
                return

            if status == "ACTIVE":
                await asyncio.sleep(0)  # yield to the event loop, then chain
                continue

            sleep_time = min(interval_min * 60, deadline - time.time())
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

        await send_dm(f"\U0001f3c1 **Auto mode finished** for `{project}` after {info['cycle']} cycles.")
        log.info(f"auto-loop {project} finished after {info['cycle']} cycles")

    except asyncio.CancelledError:
        await send_dm(f"\u26d4 **Auto mode stopped** for `{project}` after {info['cycle']} cycles.")
        log.info(f"auto-loop {project} cancelled after {info['cycle']} cycles")
    finally:
        AUTO_LOOPS.pop(project, None)


def infer_repo_name(project: str) -> str:
    """Infer GitHub repo name from project directory name.
    Could be enhanced to read .git/config, but simple mapping works for now."""
    # Most of the time the dir name matches the repo name.
    # Special case: lowercase dir name, mixed-case repo name
    known = {
        "cometcave": "CometCave",
    }
    return known.get(project.lower(), project)


# --- Socket listener ---

async def notify_listener():
    """Listen on a Unix socket for notifications and watcher commands."""
    try:
        os.unlink(NOTIFY_SOCKET)
    except FileNotFoundError:
        pass

    async def handle(reader, writer):
        try:
            data = await reader.read(16384)
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
                    result = "\n".join(lines) if lines else "(no active watchers)"
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

                # Default: treat as notification
                msg = payload.get("message", "")
            else:
                msg = text

            if msg:
                await send_dm(msg)
                log.info(f"notify -> user: {msg[:80]}")
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
        log.info(f"Home channel id: {ALLOWED_CHANNEL_ID}")
    if not hasattr(client, "_socket_task"):
        client._socket_task = asyncio.create_task(notify_listener())


def message_is_from_nick_in_allowed_place(message: discord.Message) -> bool:
    """Howl listens to:
    - DMs from the allowed user (always)
    - The configured home channel (if set), from the allowed user
    - Any channel where he's @mentioned by the allowed user
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

    if content == "!watchers":
        if not WATCHERS:
            await message.channel.send("No active watchers.")
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
            await message.channel.send("No watcher found with that ID.")
        elif len(matches) > 1:
            await message.channel.send(f"Ambiguous ID -- matches {len(matches)} watchers.")
        else:
            WATCHERS[matches[0]]["task"].cancel()
            await message.channel.send(f"Cancelled watcher `{matches[0][:8]}`")
        return

    # --- Auto mode ---
    if content.startswith("!auto"):
        parts = content.split(None)
        # !auto stop
        if len(parts) >= 2 and parts[1] == "stop":
            if len(parts) >= 3:
                proj = parts[2]
                if proj in AUTO_LOOPS:
                    AUTO_LOOPS[proj]["task"].cancel()
                    await message.channel.send(f"Stopping auto mode for `{proj}`.")
                else:
                    await message.channel.send(f"No auto loop running for `{proj}`.")
            elif AUTO_LOOPS:
                for proj, entry in list(AUTO_LOOPS.items()):
                    entry["task"].cancel()
                await message.channel.send(f"Stopping all auto loops: {', '.join(AUTO_LOOPS.keys())}")
            else:
                await message.channel.send("No auto loops running.")
            return

        # !auto status
        if len(parts) >= 2 and parts[1] == "status":
            if not AUTO_LOOPS:
                await message.channel.send("No auto loops running.")
            else:
                lines = []
                for proj, entry in AUTO_LOOPS.items():
                    info = entry["info"]
                    remaining = max(0, int((info["deadline"] - time.time()) / 60))
                    lines.append(
                        f"**{proj}** | {remaining}min left | "
                        f"interval {info['interval_min']}min | "
                        f"cycle {info['cycle']} | "
                        f"{info['instructions'][:60] or '(no instructions)'}"
                    )
                await message.channel.send("\n".join(lines))
            return

        # !auto update [project] <new instructions>
        if len(parts) >= 2 and parts[1] == "update":
            if not AUTO_LOOPS:
                await message.channel.send("No auto loops running.")
                return
            if len(parts) < 3:
                await message.channel.send(
                    "Usage: `!auto update [project] <new instructions>`"
                )
                return
            if parts[2] in AUTO_LOOPS:
                proj = parts[2]
                instr_start = 3
            elif len(AUTO_LOOPS) == 1:
                proj = next(iter(AUTO_LOOPS))
                instr_start = 2
            else:
                await message.channel.send(
                    f"Multiple loops running ({', '.join(AUTO_LOOPS.keys())}). "
                    f"Specify project: `!auto update <project> <instructions>`"
                )
                return
            if len(parts) <= instr_start:
                await message.channel.send("Provide new instructions after the project name.")
                return
            new_instr = " ".join(parts[instr_start:])
            entry_info = AUTO_LOOPS[proj]["info"]
            entry_info["instructions"] = new_instr
            next_cycle = entry_info["cycle"] + 1
            await message.channel.send(
                f"\U0001f4dd **Instructions updated** for `{proj}`. "
                f"Takes effect on cycle #{next_cycle}.\n"
                f"New: {new_instr}"
            )
            return

        # !auto <project> <duration> <interval> [instructions...]
        if len(parts) < 4:
            await message.channel.send(
                "**Usage:** `!auto <project> <duration> <interval> [instructions]`\n"
                "Example: `!auto cometcave 4h 30m focus on UX improvements`\n\n"
                "**Other:**\n"
                "`!auto status` — show running loops\n"
                "`!auto stop [project]` — stop a loop (or all)\n"
                "`!auto update [project] <instructions>` — change instructions mid-run"
            )
            return

        project = parts[1]
        duration_min = parse_duration(parts[2])
        interval_min = parse_duration(parts[3])
        instructions = " ".join(parts[4:]) if len(parts) > 4 else ""

        if duration_min <= 0 or interval_min <= 0:
            await message.channel.send("Could not parse duration or interval. Use formats like `4h`, `30m`, `2h30m`.")
            return

        if duration_min > MAX_AUTO_DURATION:
            await message.channel.send(f"Max duration is {MAX_AUTO_DURATION} minutes (24h).")
            return

        if interval_min < 5:
            await message.channel.send("Minimum interval is 5 minutes.")
            return

        if project in AUTO_LOOPS:
            await message.channel.send(f"Auto loop already running for `{project}`. Use `!auto stop {project}` first.")
            return

        repo = infer_repo_name(project)
        info = {
            "project": project,
            "repo": repo,
            "duration_min": duration_min,
            "interval_min": interval_min,
            "instructions": instructions,
            "deadline": time.time() + duration_min * 60,
            "cycle": 0,
        }
        task = asyncio.create_task(auto_loop(project, info))
        AUTO_LOOPS[project] = {"task": task, "info": info}
        return

    # --- Help ---
    if content == "!help":
        await message.channel.send(
            "**Commands:**\n"
            "`!new` — start a fresh Claude session\n"
            "`!status` — show current session info\n"
            "`!logs` — show last 20 tripwire log lines\n"
            "`!watchers` — list active watchers\n"
            "`!cancel <id>` — cancel a watcher\n"
            "`!auto <project> <duration> <interval> [instructions]` — autonomous dev loop\n"
            "`!auto status` — show running auto loops\n"
            "`!auto stop [project]` — stop auto loop\n"
            "`!auto update [project] <instructions>` — change instructions mid-run\n"
            "`!help` — this message\n"
            "\nAnything else is forwarded to Claude."
        )
        return

    # --- Forward to Claude ---
    lock = LOCKS.setdefault(channel_id, asyncio.Lock())
    async with lock:
        session_id, first_call = get_or_create_session(channel_id)
        log.info(f"forwarding (channel={channel_id} session={session_id[:8]} first={first_call}): {content[:80]}")
        try:
            await message.add_reaction("\u23f3")
        except Exception:
            pass
        try:
            async with message.channel.typing():
                reply = await run_claude(session_id, first_call, content)
        finally:
            try:
                await message.remove_reaction("\u23f3", client.user)
            except Exception:
                pass
        if first_call:
            mark_session_used(channel_id)
        try:
            await message.add_reaction("\u2705")
        except Exception:
            pass
        for chunk in split_discord(reply):
            await message.channel.send(chunk)


client.run(TOKEN, log_handler=None)
