# Autonomous Claude Code Agent on a DigitalOcean Droplet

A self-running Claude Code agent that lives on a cheap VPS, takes instructions via Discord from your phone, writes code, opens PRs, monitors CI, and manages its own task queue — with layered safety so it can't delete your repos or push broken code to main.

## What it does

You DM a Discord bot from your phone:

```
!auto cometcave 4h 30m focus on UX improvements
```

Claude runs autonomously for 4 hours, cycling every 30 minutes:
1. Checks open PRs — merges passing ones, fixes failing ones
2. Picks the next task from GitHub Issues (your backlog)
3. Implements it, opens a PR, watches CI
4. If the backlog is empty, brainstorms improvements and creates new issues
5. Notifies you on Discord at each step

You can also chat with it directly via DM for one-off tasks.

## Architecture

```
Your phone (Discord DM)
  → Discord bot (Python, systemd on host)
    → claude-sandbox launcher
      → Docker container (ephemeral, per-invocation)
        → Claude Code (--dangerously-skip-permissions + PreToolUse hook safety net)
          → Subagents: scout (Haiku), planner (Sonnet), task agents (Sonnet/Haiku)
```

### Key components

| Component | What it is | Where it lives |
|---|---|---|
| **Droplet** | Ubuntu 24.04, 2GB RAM + 2GB swap | DigitalOcean ($12/mo) |
| **Docker sandbox** | Node 20, Python 3, git, gh, Playwright+Chromium | `claude-sandbox:latest` image |
| **Discord bot** | Python (discord.py), systemd service | `/opt/claude-discord/bot.py` |
| **Launcher** | Bash script that runs Claude in Docker with correct mounts | `/usr/local/bin/claude-sandbox` |
| **Tripwire hook** | PreToolUse bash hook that blocks destructive operations | `~/.claude/tripwire.sh` (bind-mounted) |
| **Custom agents** | 5 agent definitions with model routing | `~/.claude/agents/*.md` (bind-mounted) |
| **CLAUDE.md** | Advisor pattern instructions, workflow rules | `/workspace/CLAUDE.md` |

### Model hierarchy

The advisor (Opus) thinks and delegates. Subagents run on cheaper/faster models:

| Agent | Model | Role |
|---|---|---|
| Advisor (main) | Opus | Decides what to do, reviews results, never edits files directly |
| `scout` | Haiku | Fast read-only codebase exploration |
| `planner` | Sonnet | Designs implementation plans |
| `task-trivial` | Haiku | Typos, config tweaks, one-liners |
| `task-simple` | Sonnet | Bug fixes, small features |
| `task-complex` | Sonnet | Multi-file features, can spawn sub-agents |

### Shared context and durability

Agents share context via a `.claude-scratch/` directory in each project (gitignored):
- `scout.md` — codebase findings from the scout agent
- `plan.md` — implementation plan from the planner
- `progress.md` — running log of completed steps
- `task-status.json` — checkpoint file so work resumes if a session drops

If a subagent crashes or times out, the next cycle reads `task-status.json`, checks git status on the branch, and picks up where it left off.

## Safety layers

The agent runs with `--dangerously-skip-permissions` but is sandboxed six ways:

| # | Layer | Enforced by | What it prevents |
|---|---|---|---|
| 1 | Docker container | Container runtime | No host filesystem access outside `/workspace` |
| 2 | Non-root user | Linux | `claude` user (UID 1000), no sudo |
| 3 | Firewall (ufw) | Kernel netfilter | Blocks metadata endpoint, SMTP, RFC1918 outbound |
| 4 | PreToolUse tripwire | Claude Code hooks | Blocks `rm -rf /`, force push, `gh repo delete`, `curl\|sh`, writes outside workspace |
| 5 | Fine-grained PAT | GitHub API (server-side) | No repo deletion, no visibility changes, no workflow edits |
| 6 | Branch protection | GitHub API (server-side) | PRs required, no force push, CI must pass before merge, enforce_admins |

Even if Claude goes fully rogue, it physically cannot delete a repo (PAT lacks Administration scope) or push to main (branch protection rejects it server-side).

## Discord commands

| Command | What it does |
|---|---|
| `!auto <project> <duration> <interval> [instructions]` | Start autonomous dev loop |
| `!auto status` | Show running loops |
| `!auto stop [project]` | Stop a loop |
| `!new` | Fresh Claude session |
| `!status` | Current session info |
| `!watchers` | List active CI/deploy watchers |
| `!cancel <id>` | Cancel a watcher |
| `!logs` | Last 20 tripwire log lines |
| `!help` | All commands |
| *(any other text)* | Forwarded to Claude as a one-off task |

## Tools available to Claude inside the sandbox

| Tool | Purpose |
|---|---|
| `claude-notify "msg"` | DM the user on Discord (proactive notifications) |
| `claude-watch --command "..." --match "..." --every 60 --notify "..."` | Poll a command until condition is met, then notify |
| `gh pr create/view/checks/merge` | PR lifecycle |
| `gh issue list/create/close` | Task queue management |
| Playwright MCP (headless Chromium) | Navigate, screenshot, interact with web UIs |

## How to replicate

### Prerequisites
- A DigitalOcean droplet (or any Ubuntu 24.04 VPS, 2GB+ RAM)
- Claude Max subscription (or API key)
- A Discord bot (create at discord.com/developers, enable Message Content Intent)
- A GitHub fine-grained PAT with Contents + Pull Requests (read/write), NO Administration

### Steps (abbreviated)

1. **SSH key + droplet access** — transfer your SSH key, set up `~/.ssh/config` with a Host alias

2. **Droplet setup** — swap, non-root user, ufw, Node 20, Python 3, Docker, git, gh, tmux

3. **Build the sandbox image** — Dockerfile with Node, Python, git, gh, Claude Code, Playwright

4. **Write the launcher** — `/usr/local/bin/claude-sandbox` script that runs Docker with the right bind mounts and env

5. **Write the tripwire** — `tripwire.sh` PreToolUse hook + `settings.json` wiring it. Test with fake payloads

6. **GitHub setup** — fine-grained PAT (no Admin), branch protection on active repos, required status checks for Vercel/CI

7. **Claude login** — `claude login` inside the container (interactive, paste URL to your browser). Credentials persist via bind mount

8. **Discord bot** — `bot.py` with discord.py, systemd unit, config with bot token + allowed user ID

9. **Notification tools** — `claude-notify` and `claude-watch` CLIs, Unix socket in the bot, bind-mounted into the container

10. **Custom agents** — `.claude/agents/*.md` with model assignments in frontmatter

11. **CLAUDE.md** — advisor pattern, agent delegation table, shared context, durability rules

### Time estimate
The full setup from scratch takes about 2-3 hours with an experienced Claude Code user driving it. Most of that is iterating on the tripwire rules and testing edge cases.

## Costs

| Item | Cost |
|---|---|
| DigitalOcean droplet (2GB) | $12/mo |
| Claude Max subscription | $100-200/mo (shared with your normal usage) |
| GitHub | Free (public repos) |
| Discord bot | Free |
| **Total** | **~$12/mo + your existing Claude subscription** |

## Known limitations

- **2GB RAM is tight.** Playwright + Node dev server + Claude can slow down. Bump to 4GB ($24/mo) if it's a problem.
- **No streaming.** Discord replies arrive all at once after Claude finishes, not streamed. Long tasks show a typing indicator.
- **Session compaction.** Long autonomous runs may hit context limits. The durability layer (task-status.json) mitigates this but doesn't eliminate it.
- **Single-threaded per project.** The auto loop runs one task at a time per project. Parallel tasks across different projects work fine.
- **Max plan rate limits.** Claude Max has per-5-hour-window usage caps. Long auto runs may hit the ceiling and pause until the window resets.
