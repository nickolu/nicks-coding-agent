#!/usr/bin/env python3
"""Sophie <> Discord bridge. Forwards DMs from an allowed user to sophie-sandbox -p.

Sophie is Nick's chief of staff / personal manager (see sophie-config/CLAUDE.md).
This bot is the same shape as the Howl bot but stripped of the autonomous-coding
loop and CI watcher features — Sophie doesn't open PRs, so she doesn't need them.

The watcher framework is kept (and renamed in the user-facing help) because it's
generically useful for time-based reminders and polling Google Tasks / Calendar.
"""
import asyncio
import json
import logging
import os
import socket
import subprocess
import time
import uuid
from pathlib import Path

import discord

CONFIG_PATH = Path("/etc/sophie-discord/config.json")
STATE_PATH = Path("/var/lib/sophie-discord/sessions.json")
TRIPWIRE_LOG = Path("/home/sophie/.claude-container/tripwire.log")
SOPHIE_CMD = "/usr/local/bin/sophie-sandbox"
NOTIFY_SOCKET = "/run/sophie-discord.sock"
SOPHIE_TIMEOUT = None  # no timeout
MAX_WATCHER_TIMEOUT = 1440  # 24 hours in minutes
DEFAULT_WATCHER_TIMEOUT = 60  # 1 hour in minutes

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


async def run_sophie(session_id: str, first_call: bool, prompt: str) -> str:
    """Run sophie-sandbox -p in a thread (blocking subprocess) and return stdout."""
    if first_call:
        args = [SOPHIE_CMD, "-p", "--session-id", session_id, prompt]
    else:
        args = [SOPHIE_CMD, "-p", "-r", session_id, prompt]

    def _run():
        try:
            r = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=SOPHIE_TIMEOUT,
            )
            if r.returncode != 0:
                return f"[sophie error rc={r.returncode}]\n{r.stderr.strip() or r.stdout.strip()}"
            return r.stdout.strip() or "(empty response)"
        except subprocess.TimeoutExpired:
            return f"[sophie timed out after {SOPHIE_TIMEOUT}s]"
        except Exception as e:
            return f"[bot error: {e}]"

    return await asyncio.to_thread(_run)


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


async def send_dm(msg: str):
    """Send a message to Nick — DM if no allowed channel is configured,
    else post in the allowed channel."""
    if ALLOWED_CHANNEL_ID:
        channel = client.get_channel(ALLOWED_CHANNEL_ID) or await client.fetch_channel(ALLOWED_CHANNEL_ID)
        for chunk in split_discord(msg):
            await channel.send(chunk)
        return
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
        log.info(f"Allowed channel id: {ALLOWED_CHANNEL_ID}")
    if not hasattr(client, "_socket_task"):
        client._socket_task = asyncio.create_task(notify_listener())


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

    # --- Help ---
    if content == "!help":
        await message.channel.send(
            "**Commands:**\n"
            "`!new` — start a fresh session with Sophie\n"
            "`!status` — show current session info\n"
            "`!logs` — show last 20 tripwire log lines\n"
            "`!reminders` — list active reminders/watchers\n"
            "`!cancel <id>` — cancel a reminder\n"
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


client.run(TOKEN, log_handler=None)
