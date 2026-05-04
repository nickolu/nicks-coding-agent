# claude-bridge

Source for **Pendragon & Co** — the household of autonomous Claude Code agents
that runs on a DigitalOcean droplet and is driven from Discord.

| Agent | Role | Driven from |
|---|---|---|
| **Howl** | Engineer / coding agent. Writes code, opens PRs, monitors CI. | Discord (DM the Howl bot, or `#howl` channel) |
| **Sophie** | Chief of staff / personal manager. Goals, journal, reminders, Google Tasks/Calendar/Gmail. | Discord (DM the Sophie bot, or `#sophie` channel) |

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
| `claude-config/tripwire.sh` | `/home/claude/.claude-container/tripwire.sh` (chmod +x, bind-mounted as `/home/claude/.claude/tripwire.sh`) |
| `claude-config/.env.example` | `/home/claude/.claude-container/.env` (fill in real values, mode `0600`) |
| `claude-config/agents/*.md` | `/home/claude/.claude-container/agents/*.md` (bind-mounted into the container) |
| `tools/claude-notify` | `/home/claude/.claude-container/claude-notify` (chmod +x) |
| `tools/claude-watch` | `/home/claude/.claude-container/claude-watch` (chmod +x) |
| `systemd/claude-discord.service` | `/etc/systemd/system/claude-discord.service` |

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
| `sophie-systemd/sophie-discord.service` | `/etc/systemd/system/sophie-discord.service` |

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
- restarts `sophie-discord` if Sophie's `bot.py` or systemd unit changed
- runs `systemctl daemon-reload` if any systemd unit changed
- prints a reminder to rebuild the relevant Docker image if a Dockerfile changed

Re-running with no upstream changes is a no-op.

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
ssh droplet 'cat /etc/systemd/system/claude-discord.service' > systemd/claude-discord.service

# Sophie
scp droplet:/opt/sophie-discord/bot.py sophie-bot/bot.py
scp droplet:/usr/local/bin/sophie-sandbox sophie-launcher/sophie-sandbox
scp droplet:/home/sophie/sandbox/Dockerfile sophie-sandbox/Dockerfile
scp droplet:/home/sophie/.claude-container/{tripwire.sh,settings.json} sophie-config/
scp droplet:/home/sophie/notebook/CLAUDE.md sophie-config/CLAUDE.md
scp droplet:/home/sophie/.claude-container/{sophie-notify,sophie-watch} sophie-tools/
ssh droplet 'cat /etc/systemd/system/sophie-discord.service' > sophie-systemd/sophie-discord.service
```
