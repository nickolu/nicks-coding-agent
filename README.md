# claude-bridge

Source for **Pendragon & Co** — the household of autonomous Claude Code agents
that runs on a DigitalOcean droplet and is driven from Discord.

| Agent | Role | Driven from |
|---|---|---|
| **Howl** | Engineer / coding agent. Writes code, opens PRs, monitors CI. | Discord (DM the Howl bot, or `#howl` channel) |
| **Sophie** | Chief of staff / personal manager. Goals, journal, reminders, Google Tasks/Calendar/Gmail. | Discord (DM the Sophie bot, or `#sophie` channel) |
| **Calcifer** | D&D dungeon-master assistant. Encounters, NPCs, session recaps, art. | Discord (DM the Calcifer bot, or `#calcifer` channel) |

The "what Howl does and why" lives in [`claud-droplet-agent.md`](./claud-droplet-agent.md).
This README is the **file-to-host-path map** for actually deploying or restoring both agents.

## File map

### Howl

| Repo path | Host path on the droplet |
|---|---|
| `discord-bot/bot.py` | `/opt/claude-discord/bot.py` |
| `discord-bot/config.json.example` | `/etc/claude-discord/config.json` (fill in real values, mode `0600`) |
| `launcher/claude-sandbox` | `/usr/local/bin/claude-sandbox` (chmod +x) |
| `sandbox/Dockerfile` | `/home/claude/sandbox/Dockerfile` — build with `docker build -t claude-sandbox:latest .` |
| `claude-config/settings.json` | `/home/claude/.claude-container/settings.json` (bind-mounted into the container as `/home/claude/.claude/settings.json`) |
| `claude-config/CLAUDE.md` | `/home/claude/projects/CLAUDE.md` (Howl's workspace instructions / advisor pattern, read as `/workspace/CLAUDE.md`) |
| `claude-config/tripwire.sh` | `/home/claude/.claude-container/tripwire.sh` (chmod +x, bind-mounted as `/home/claude/.claude/tripwire.sh`) |
| `claude-config/.env.example` | `/home/claude/.claude-container/.env` (fill in real values, mode `0600`) |
| `claude-config/agents/*.md` | `/home/claude/.claude-container/agents/*.md` (bind-mounted into the container) |
| `tools/claude-notify` | `/home/claude/.claude-container/claude-notify` (chmod +x) |
| `tools/claude-watch` | `/home/claude/.claude-container/claude-watch` (chmod +x) |
| `systemd/claude-discord.service` | `/etc/systemd/system/claude-discord.service` |
| `tools/agent-auth-status` | `/usr/local/bin/agent-auth-status` (chmod +x) — cross-agent auth check |
| `systemd/agent-auth-check.cron` | `/etc/cron.d/agent-auth-check` — daily auth check, pings #howl on trouble |

### Sophie

| Repo path | Host path on the droplet |
|---|---|
| `sophie-bot/bot.py` | `/opt/sophie-discord/bot.py` |
| `sophie-bot/config.json.example` | `/etc/sophie-discord/config.json` (fill in real values, mode `0600`) |
| `sophie-launcher/sophie-sandbox` | `/usr/local/bin/sophie-sandbox` (chmod +x) |
| `sophie-sandbox/Dockerfile` | `/home/sophie/sandbox/Dockerfile` — build with `docker build -t sophie-sandbox:latest .` |
| `sophie-config/settings.json` | `/home/sophie/.claude-container/settings.json` (bind-mounted as `/home/sophie/.claude/settings.json`) |
| `sophie-config/tripwire.sh` | `/home/sophie/.claude-container/tripwire.sh` (bind-mounted as `/home/sophie/.claude/tripwire.sh`) |
| `sophie-config/.env.example` | `/home/sophie/.claude-container/.env` (fill in real values, mode `0600`) |
| `sophie-config/CLAUDE.md` | `/home/sophie/notebook/CLAUDE.md` (Sophie's persona / instructions) |
| `sophie-tools/sophie-notify` | `/home/sophie/.claude-container/sophie-notify` (chmod +x) |
| `sophie-tools/sophie-watch` | `/home/sophie/.claude-container/sophie-watch` (chmod +x) |
| `sophie-tools/sophie-image`, `sophie-attach`, `sophie-schedule`, `sophie-recent-dms`, `sophie-recent-howl`, `sophie-task-howl`, `sophie-await-howl-reply` | `/home/sophie/.claude-container/<name>` (chmod +x) |
| `sophie-systemd/sophie-discord.service` | `/etc/systemd/system/sophie-discord.service` |

### Calcifer

| Repo path | Host path on the droplet |
|---|---|
| `calcifer-bot/bot.py` | `/opt/calcifer-discord/bot.py` |
| `calcifer-bot/config.json.example` | `/etc/calcifer-discord/config.json` (fill in real values, mode `0600`) |
| `calcifer-launcher/calcifer-sandbox` | `/usr/local/bin/calcifer-sandbox` (chmod +x) |
| `calcifer-sandbox/Dockerfile` | `/home/calcifer/sandbox/Dockerfile` — build with `docker build -t calcifer-sandbox:latest .` |
| `calcifer-config/settings.json` | `/home/calcifer/.claude-container/settings.json` (bind-mounted as `/home/calcifer/.claude/settings.json`) |
| `calcifer-config/tripwire.sh` | `/home/calcifer/.claude-container/tripwire.sh` (bind-mounted as `/home/calcifer/.claude/tripwire.sh`) |
| `calcifer-config/CLAUDE.md` | `/home/calcifer/notebook/CLAUDE.md` (Calcifer's persona) |
| `calcifer-tools/calcifer-notify`, `calcifer-watch`, `calcifer-image`, `calcifer-attach`, `calcifer-schedule`, `calcifer-recent-dms` | `/home/calcifer/.claude-container/<name>` (chmod +x) |
| `calcifer-systemd/calcifer-discord.service` | `/etc/systemd/system/calcifer-discord.service` |

## What is **not** in this repo

These live only on the host because they are secrets or runtime state:

**Howl:**
- `/etc/claude-discord/config.json` — Discord bot token + allowed user ID
- `/home/claude/.claude-container/.credentials.json` — Claude login credentials
- `/home/claude/.claude-container/.env` — `GH_TOKEN` and git identity
- per-session state in `/home/claude/.claude-container/{sessions,projects,cache,plans,…}/`
- `/var/lib/claude-discord/sessions.json`

**Sophie:**
- `/etc/sophie-discord/config.json` — Discord bot token + allowed user ID + (optional) channel ID
- `/home/sophie/.claude-container/.credentials.json` — Claude login credentials (same Anthropic account as Howl, but a separate login session)
- `/home/sophie/notebook/personal-goals/` — Nick's personal goals docs (synced from his laptop)
- `/home/sophie/notebook/journal/` — daily entries
- per-session state in `/home/sophie/.claude-container/{sessions,projects,cache,plans,…}/`
- `/var/lib/sophie-discord/sessions.json`

**Calcifer:**
- `/etc/calcifer-discord/config.json` — Discord bot token + allowed user ID + (optional) channel ID
- `/home/calcifer/.claude-container/.credentials.json` — Claude login credentials
- `/home/calcifer/notebook/dnd/` — campaign notes (per-campaign subfolders)
- `/home/calcifer/notebook/generated-images/` — image output (NPC portraits, locations)
- per-session state in `/home/calcifer/.claude-container/{sessions,projects,cache,plans,…}/`
- `/var/lib/calcifer-discord/sessions.json`

## Claude auth — how it works and how to renew

Each agent authenticates its `claude` CLI with a long-lived **subscription
`setup-token`**, stored as `CLAUDE_CODE_OAUTH_TOKEN` in that agent's
`.claude-container/.env` and passed into every container via `--env-file`. It's
valid ~1 year. (An older `.credentials.json` OAuth file may also be present but
is inert once the env token is set — the env var wins.)

**Symptom of a dead token:** the agent replies `Failed to authenticate. API
Error: 401`. Two distinct messages tell you *where* it's failing:
`Invalid authentication credentials` = env var missing/unread (falling back to
the creds file); `Invalid bearer token` = env var is read but the value is bad.

**Renew (interactive — needs Nick's browser):**

```sh
ssh -t droplet /usr/local/bin/<agent>-sandbox shell   # claude- / sophie- / calcifer-
# inside the container:
claude setup-token          # paste the *browser code* at this prompt
# it prints:  Your OAuth token (valid for 1 year): sk-ant-oat01-…
# copy THAT line (starts with sk-ant-oat01-), then still inside the container:
sed -i '/^CLAUDE_CODE_OAUTH_TOKEN=/d' /home/<user>/.claude/.env && \
  read -rsp "paste token: " T && echo && \
  case "$T" in sk-ant-oat01-*) \
    printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\n' "$T" >> /home/<user>/.claude/.env && \
    printf '%s\n' "$(date -u -d '+1 year' +%Y-%m-%d)" > /home/<user>/.claude/.claude-token-expiry && \
    echo "appended ok (${#T} chars)";; \
  *) echo "REJECTED: not the token — look for the sk-ant-oat01- line";; esac
```

`/home/<user>/.claude` inside the container **is** the host's
`.claude-container` (bind-mounted), so this writes through to the host. No
systemd restart needed — each call spawns a fresh `--rm` container that
re-reads the file. Two easy mistakes, both guarded above: pasting the browser
*authorization code* instead of the printed *token*, and a truncated copy — the
`case` guard rejects anything not starting with `sk-ant-oat01-`.

**Health check.** `agent-auth-status` (on the droplet, run as root) audits all
agents — mechanism, expiry, and a live auth probe — and exits non-zero if any
is broken or within 14 days of a recorded expiry. A daily cron
(`/etc/cron.d/agent-auth-check`) runs it with `--alert`, which pings **#howl**
on trouble, so a dead token surfaces same-day instead of whenever it's next
used. The `.claude-token-expiry` sidecar (written by the renew command above)
is what enables the *proactive* 14-day warning; without it an agent is still
covered by the live probe, just not warned ahead of time.

## One-time setup for Sophie

The first time you bring Sophie up on the droplet, do this in order. The deploy
script (`sync.sh`) is **idempotent** and only installs Sophie's files if the
`sophie` user exists, so nothing here breaks an existing Howl-only deploy.

1. **Discord — create the server and bot.**
   - Create the **Pendragon & Co** Discord server (or reuse an existing one).
   - Create text channels `#howl` and `#sophie`.
   - At <https://discord.com/developers/applications>, create a new application called **Sophie**, add a Bot, enable **Message Content Intent**, copy the bot token.
   - Invite the bot to the Pendragon & Co server, give it permissions in `#sophie` only.
   - Get the channel ID for `#sophie` (right-click → Copy ID with Developer Mode on).

2. **Droplet — create the `sophie` user and dirs.**
   ```sh
   sudo groupadd -g 1001 sophie
   sudo useradd -m -u 1001 -g 1001 -G docker -s /bin/bash sophie
   sudo install -d -o sophie -g sophie -m 0755 \
       /home/sophie/notebook \
       /home/sophie/notebook/personal-goals \
       /home/sophie/notebook/journal \
       /home/sophie/.claude-container \
       /home/sophie/sandbox \
       /opt/sophie-discord \
       /etc/sophie-discord \
       /var/lib/sophie-discord
   sudo chown root:root /etc/sophie-discord /opt/sophie-discord /var/lib/sophie-discord
   ```

3. **Deploy the files.**
   ```sh
   cd /opt/claude-bridge
   sudo git pull
   sudo ./sync.sh
   ```

4. **Bot config.**
   ```sh
   sudo cp /opt/claude-bridge/sophie-bot/config.json.example /etc/sophie-discord/config.json
   sudo nano /etc/sophie-discord/config.json   # fill in bot_token, allowed_user_id, allowed_channel_id
   sudo chmod 0600 /etc/sophie-discord/config.json
   ```

5. **Bot Python venv.**
   ```sh
   sudo python3 -m venv /opt/sophie-discord/venv
   sudo /opt/sophie-discord/venv/bin/pip install discord.py
   ```

6. **Build Sophie's Docker image.**
   ```sh
   sudo docker build -t sophie-sandbox:latest /home/sophie/sandbox
   ```

7. **Create a Google OAuth client for `gws`.**
   Sophie talks to Google via the [`@googleworkspace/cli`](https://www.npmjs.com/package/@googleworkspace/cli)
   tool (binary: `gws`), already installed in her image. It needs a Desktop OAuth client.
   - Go to <https://console.cloud.google.com/apis/credentials> in a Google project that has
     Gmail / Calendar / Drive / Tasks / Docs APIs enabled.
   - Create credentials → **OAuth client ID** → application type **Desktop app**.
   - Copy the client ID and secret into `/home/sophie/.claude-container/.env`:
     ```
     GOOGLE_WORKSPACE_CLI_CLIENT_ID=...
     GOOGLE_WORKSPACE_CLI_CLIENT_SECRET=...
     ```

8. **`claude login` and `gws auth login` inside Sophie's container** (one-time, interactive).
   ```sh
   sudo -u sophie /usr/local/bin/sophie-sandbox shell
   # inside the container:
   claude login          # paste URL into your browser, approve
   gws auth login        # same — pastes a URL, you approve, token cached in keyring
   exit
   ```
   `claude login` populates `/home/sophie/.claude-container/.credentials.json`.
   `gws auth login` stores the OAuth refresh token via libsecret so subsequent
   `gws` calls work non-interactively.

9. **Migrate personal-goals from your laptop.**
   ```sh
   # on the laptop:
   rsync -av ~/Projects/personal-goals/ droplet:/tmp/personal-goals/
   # on the droplet:
   sudo rsync -av --chown=sophie:sophie /tmp/personal-goals/ /home/sophie/notebook/personal-goals/
   sudo rm -rf /tmp/personal-goals
   ```

10. **Start the bot.**
    ```sh
    sudo systemctl enable --now sophie-discord
    sudo systemctl status sophie-discord
    ```
    Then DM Sophie or post in `#sophie` — she should reply.

## One-time setup for Calcifer

Same shape as Sophie's setup. `sync.sh` skips Calcifer files entirely until the
`calcifer` user exists, so this is safe to run on a Howl-only / Howl+Sophie droplet.

1. **Discord — create the bot.**
   - In **Pendragon & Co**, create a `#calcifer` channel.
   - At <https://discord.com/developers/applications>, create a new application **Calcifer**, add a Bot, enable **Message Content Intent**, copy the bot token.
   - Invite the bot to the server with permissions in `#calcifer` only.
   - Right-click `#calcifer` (with Developer Mode on) → Copy ID.

2. **Droplet — create the `calcifer` user and dirs.**
   ```sh
   sudo groupadd -g 1002 calcifer
   sudo useradd -m -u 1002 -g 1002 -G docker -s /bin/bash calcifer
   sudo install -d -o calcifer -g calcifer -m 0755 \
       /home/calcifer/notebook \
       /home/calcifer/notebook/dnd \
       /home/calcifer/notebook/generated-images \
       /home/calcifer/.claude-container \
       /home/calcifer/sandbox \
       /opt/calcifer-discord \
       /etc/calcifer-discord \
       /var/lib/calcifer-discord
   sudo chown root:root /etc/calcifer-discord /opt/calcifer-discord /var/lib/calcifer-discord
   ```

3. **Deploy the files.**
   ```sh
   cd /opt/claude-bridge
   sudo git pull
   sudo ./sync.sh
   ```

4. **Bot config.**
   ```sh
   sudo cp /opt/claude-bridge/calcifer-bot/config.json.example /etc/calcifer-discord/config.json
   sudo nano /etc/calcifer-discord/config.json   # fill bot_token, allowed_user_id, allowed_channel_id
   sudo chmod 0600 /etc/calcifer-discord/config.json
   ```

5. **Bot Python venv.**
   ```sh
   sudo python3 -m venv /opt/calcifer-discord/venv
   sudo /opt/calcifer-discord/venv/bin/pip install discord.py
   ```

6. **Build Calcifer's Docker image.**
   ```sh
   sudo docker build -t calcifer-sandbox:latest /home/calcifer/sandbox
   ```

7. **Add API keys for image gen.**
   Put `OPENAI_API_KEY=...` and `GOOGLE_API_KEY=...` in `/home/calcifer/.claude-container/.env`
   (mode `0600`). The same keys Sophie uses are fine; Calcifer has his own copy of
   `.env` so each agent's secrets are isolated.

8. **`claude login` inside Calcifer's container** (one-time, interactive).
   ```sh
   sudo -u calcifer /usr/local/bin/calcifer-sandbox shell
   # inside the container:
   claude login        # paste URL into your browser, approve
   exit
   ```

9. **(Optional) Migrate existing campaign notes.**
   ```sh
   # on the laptop, if you have D&D notes already:
   rsync -av ~/dnd-notes/ droplet:/tmp/dnd/
   # on the droplet:
   sudo rsync -av --chown=calcifer:calcifer /tmp/dnd/ /home/calcifer/notebook/dnd/
   sudo rm -rf /tmp/dnd
   ```

10. **Start the bot.**
    ```sh
    sudo systemctl enable --now calcifer-discord
    sudo systemctl status calcifer-discord
    ```
    Then DM Calcifer or post in `#calcifer` — he should reply.

## Deploy workflow (steady state)

The droplet has a clone of this repo at `/opt/claude-bridge`. To ship a change:

```sh
# on the laptop
git add … && git commit -m "…" && git push

# on the droplet
ssh droplet
cd /opt/claude-bridge
sudo git pull
sudo ./sync.sh -n      # dry-run: shows what would change and what restarts
sudo ./sync.sh         # apply
```

`sync.sh` copies each repo file to its host path with the correct
owner/perms, then conditionally:

- restarts `claude-discord` if Howl's `bot.py` or systemd unit changed
- restarts `sophie-discord` if Sophie's `bot.py`, a sibling module in
  `sophie-bot/` (e.g. `sched_time.py`), or the systemd unit changed
- runs `systemctl daemon-reload` if any systemd unit changed
- prints a reminder to rebuild the relevant Docker image if a Dockerfile changed

Re-running with no upstream changes is a no-op.

## Running tests

Pure logic that the bots depend on (currently `sophie-bot/sched_time.py`,
the DST-safe recurrence math) lives in small modules next to `bot.py` and is
covered by pytest in `tests/`. Run them on your laptop, never on the droplet:

```sh
python3 -m pytest tests/ -q
```

`tests/conftest.py` puts `sophie-bot/` on `sys.path`. Do not import `bot.py`
itself in a test: it reads `/etc/sophie-discord/config.json` and imports
`discord` at import time.

## Pulling ad-hoc changes back from the droplet

If you edit a file directly on the droplet and want to bring it into the repo
to commit, use `scp` (or just `git add` it on the droplet and push from
`/opt/claude-bridge` — but only after running `sync.sh` once so the repo and
host paths actually agree).

```sh
# Howl
scp droplet:/opt/claude-discord/bot.py discord-bot/bot.py
scp droplet:/usr/local/bin/claude-sandbox launcher/claude-sandbox
scp droplet:/home/claude/sandbox/Dockerfile sandbox/Dockerfile
scp 'droplet:/home/claude/.claude-container/agents/*.md' claude-config/agents/
scp droplet:/home/claude/.claude-container/{claude-notify,claude-watch} tools/
scp droplet:/home/claude/.claude-container/{tripwire.sh,settings.json} claude-config/
scp droplet:/home/claude/projects/CLAUDE.md claude-config/CLAUDE.md
ssh droplet 'cat /etc/systemd/system/claude-discord.service' > systemd/claude-discord.service

# Sophie
scp droplet:/opt/sophie-discord/bot.py sophie-bot/bot.py
scp droplet:/usr/local/bin/sophie-sandbox sophie-launcher/sophie-sandbox
scp droplet:/home/sophie/sandbox/Dockerfile sophie-sandbox/Dockerfile
scp droplet:/home/sophie/.claude-container/{tripwire.sh,settings.json} sophie-config/
scp droplet:/home/sophie/notebook/CLAUDE.md sophie-config/CLAUDE.md
scp droplet:/home/sophie/.claude-container/{sophie-notify,sophie-watch} sophie-tools/
ssh droplet 'cat /etc/systemd/system/sophie-discord.service' > sophie-systemd/sophie-discord.service

# Calcifer
scp droplet:/opt/calcifer-discord/bot.py calcifer-bot/bot.py
scp droplet:/usr/local/bin/calcifer-sandbox calcifer-launcher/calcifer-sandbox
scp droplet:/home/calcifer/sandbox/Dockerfile calcifer-sandbox/Dockerfile
scp droplet:/home/calcifer/.claude-container/{tripwire.sh,settings.json} calcifer-config/
scp droplet:/home/calcifer/notebook/CLAUDE.md calcifer-config/CLAUDE.md
scp 'droplet:/home/calcifer/.claude-container/calcifer-*' calcifer-tools/
ssh droplet 'cat /etc/systemd/system/calcifer-discord.service' > calcifer-systemd/calcifer-discord.service
```
