# claude-bridge

Source for the autonomous Claude Code agent that runs on a DigitalOcean droplet
and is driven from Discord. The "what it does" and "why" live in
[`claud-droplet-agent.md`](./claud-droplet-agent.md); this README is the
**file-to-host-path map** for actually deploying or restoring it.

## File map

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
| `systemd/claude-discord.service` | `/etc/systemd/system/claude-discord.service` (then `systemctl daemon-reload && systemctl enable --now claude-discord`) |

## What is **not** in this repo

These live only on the host because they are secrets or runtime state:

- `/etc/claude-discord/config.json` — Discord bot token + allowed user ID
- `/home/claude/.claude-container/.credentials.json` — Claude login credentials
- `/home/claude/.claude-container/.env` — `GH_TOKEN` and git identity
- `/home/claude/.claude-container/{sessions,projects,cache,plans,backups,file-history,shell-snapshots,telemetry,session-env}/` — per-session state
- `/home/claude/.claude-container/{claude.json,history.jsonl,tripwire.log}` — runtime state and logs
- `/var/lib/claude-discord/sessions.json` — bot session state

## Updating from the droplet

```sh
scp droplet:/opt/claude-discord/bot.py discord-bot/bot.py
scp droplet:/usr/local/bin/claude-sandbox launcher/claude-sandbox
scp droplet:/home/claude/sandbox/Dockerfile sandbox/Dockerfile
scp 'droplet:/home/claude/.claude-container/agents/*.md' claude-config/agents/
scp droplet:/home/claude/.claude-container/{claude-notify,claude-watch} tools/
scp droplet:/home/claude/.claude-container/{tripwire.sh,settings.json} claude-config/
ssh droplet 'cat /etc/systemd/system/claude-discord.service' > systemd/claude-discord.service
```
