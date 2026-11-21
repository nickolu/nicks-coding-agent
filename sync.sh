#!/usr/bin/env bash
# sync.sh — install repo files to their host paths and trigger the right reloads.
#
# Usage (run on the droplet, as root):
#   sudo ./sync.sh           apply changes
#   sudo ./sync.sh -n        dry run (show what would change)
#
# Idempotent: re-running with no upstream changes is a no-op.

set -euo pipefail

DRY_RUN=0
[[ "${1:-}" == "-n" || "${1:-}" == "--dry-run" ]] && DRY_RUN=1

if [[ $EUID -ne 0 ]]; then
  echo "sync.sh must run as root (use sudo)" >&2
  exit 1
fi

REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO"

CHANGED=()

# install_file <src-relative-to-repo> <dst-absolute> <owner> <group> <mode>
install_file() {
  local src="$REPO/$1" dst="$2" owner="$3" group="$4" mode="$5"
  if [[ -e "$dst" ]] && cmp -s "$src" "$dst"; then
    return 0
  fi
  CHANGED+=("$dst")
  if (( DRY_RUN )); then
    echo "would install: $1 -> $dst ($owner:$group $mode)"
    return 0
  fi
  install -D -o "$owner" -g "$group" -m "$mode" "$src" "$dst"
}

# install_dir_contents <src-dir-relative> <dst-dir-absolute> <owner> <group> <mode>
install_dir_contents() {
  local src_dir="$REPO/$1" dst_dir="$2" owner="$3" group="$4" mode="$5"
  local src
  for src in "$src_dir"/*; do
    [[ -f "$src" ]] || continue
    install_file "${src#$REPO/}" "$dst_dir/$(basename "$src")" "$owner" "$group" "$mode"
  done
}

# --- the install table ---
# === Howl (engineer / coding agent) ===
# Discord bot (root-owned, runs under systemd)
install_file discord-bot/bot.py                       /opt/claude-discord/bot.py                          root   root   0755

# Launcher
install_file launcher/claude-sandbox                  /usr/local/bin/claude-sandbox                       root   root   0755

# Sandbox Dockerfile (rebuild is manual — see end of script)
install_file sandbox/Dockerfile                       /home/claude/sandbox/Dockerfile                     claude claude 0644

# Claude config — bind-mounted into the per-invocation container
install_file claude-config/settings.json              /home/claude/.claude-container/settings.json        claude claude 0644
install_file claude-config/tripwire.sh                /home/claude/.claude-container/tripwire.sh          claude claude 0755
install_file claude-config/CLAUDE.md                  /home/claude/projects/CLAUDE.md                     claude claude 0644
install_dir_contents claude-config/agents             /home/claude/.claude-container/agents               claude claude 0644

# Notify CLIs — bind-mounted into the container
install_file tools/claude-notify                      /home/claude/.claude-container/claude-notify        claude claude 0755
install_file tools/claude-watch                       /home/claude/.claude-container/claude-watch         claude claude 0755
install_file tools/howl-post                          /home/claude/.claude-container/howl-post            claude claude 0755
install_file tools/howl-session-report                /home/claude/.claude-container/howl-session-report  claude claude 0755

# systemd unit
install_file systemd/claude-discord.service           /etc/systemd/system/claude-discord.service          root   root   0644

# === Ops: cross-agent auth health check ===
# Audits Howl/Sophie/Calcifer Claude auth daily and pings #howl on trouble.
# Not gated on any agent user — it self-skips agents whose user doesn't exist.
install_file tools/agent-auth-status                  /usr/local/bin/agent-auth-status                    root   root   0755
install_file systemd/agent-auth-check.cron            /etc/cron.d/agent-auth-check                        root   root   0644

# === Sophie (chief of staff / personal manager) ===
# Only installed if the sophie OS user exists. This lets Howl-only droplets stay clean.
if id sophie >/dev/null 2>&1; then
  install_file sophie-bot/bot.py                      /opt/sophie-discord/bot.py                          root   root   0755
  install_file sophie-bot/sched_time.py               /opt/sophie-discord/sched_time.py                   root   root   0644
  install_file sophie-launcher/sophie-sandbox         /usr/local/bin/sophie-sandbox                       root   root   0755
  install_file sophie-sandbox/Dockerfile              /home/sophie/sandbox/Dockerfile                     sophie sophie 0644
  install_file sophie-config/settings.json            /home/sophie/.claude-container/settings.json        sophie sophie 0644
  install_file sophie-config/tripwire.sh              /home/sophie/.claude-container/tripwire.sh          sophie sophie 0755
  install_file sophie-config/CLAUDE.md                /home/sophie/notebook/CLAUDE.md                     sophie sophie 0644
  install_file sophie-tools/sophie-notify             /home/sophie/.claude-container/sophie-notify        sophie sophie 0755
  install_file sophie-tools/sophie-watch              /home/sophie/.claude-container/sophie-watch         sophie sophie 0755
  install_file sophie-tools/sophie-image              /home/sophie/.claude-container/sophie-image         sophie sophie 0755
  install_file sophie-tools/sophie-attach             /home/sophie/.claude-container/sophie-attach        sophie sophie 0755
  install_file sophie-tools/sophie-schedule           /home/sophie/.claude-container/sophie-schedule      sophie sophie 0755
  install_file sophie-tools/sophie-recent-dms         /home/sophie/.claude-container/sophie-recent-dms    sophie sophie 0755
  install_file sophie-tools/sophie-recent-howl        /home/sophie/.claude-container/sophie-recent-howl   sophie sophie 0755
  install_file sophie-tools/sophie-recent-digests     /home/sophie/.claude-container/sophie-recent-digests sophie sophie 0755
  install_file sophie-tools/sophie-since-last-dm      /home/sophie/.claude-container/sophie-since-last-dm sophie sophie 0755
  install_file sophie-tools/pp-craigslist             /home/sophie/.claude-container/pp-craigslist        sophie sophie 0755
  install_file sophie-tools/pp-wikipedia              /home/sophie/.claude-container/pp-wikipedia         sophie sophie 0755
  install_file sophie-tools/sophie-task-howl          /home/sophie/.claude-container/sophie-task-howl     sophie sophie 0755
  install_file sophie-tools/sophie-await-howl-reply   /home/sophie/.claude-container/sophie-await-howl-reply sophie sophie 0755
  install_file sophie-tools/sophie-pulse-self         /home/sophie/.claude-container/sophie-pulse-self    sophie sophie 0755
  install_file sophie-tools/sophie-calendar-agenda    /home/sophie/.claude-container/sophie-calendar-agenda sophie sophie 0755
  install_file sophie-systemd/sophie-discord.service  /etc/systemd/system/sophie-discord.service          root   root   0644
else
  echo "(skipping sophie-* install table: sophie user does not exist)"
fi

# === Calcifer (D&D dungeon-master assistant) ===
# Only installed if the calcifer OS user exists. Same pattern as Sophie —
# keeps Howl-only / Sophie-only droplets clean.
if id calcifer >/dev/null 2>&1; then
  install_file calcifer-bot/bot.py                      /opt/calcifer-discord/bot.py                          root     root     0755
  install_file calcifer-launcher/calcifer-sandbox       /usr/local/bin/calcifer-sandbox                       root     root     0755
  install_file calcifer-sandbox/Dockerfile              /home/calcifer/sandbox/Dockerfile                     calcifer calcifer 0644
  install_file calcifer-config/settings.json            /home/calcifer/.claude-container/settings.json        calcifer calcifer 0644
  install_file calcifer-config/tripwire.sh              /home/calcifer/.claude-container/tripwire.sh          calcifer calcifer 0755
  install_file calcifer-config/CLAUDE.md                /home/calcifer/notebook/CLAUDE.md                     calcifer calcifer 0644
  install_file calcifer-tools/calcifer-notify           /home/calcifer/.claude-container/calcifer-notify      calcifer calcifer 0755
  install_file calcifer-tools/calcifer-watch            /home/calcifer/.claude-container/calcifer-watch       calcifer calcifer 0755
  install_file calcifer-tools/calcifer-image            /home/calcifer/.claude-container/calcifer-image       calcifer calcifer 0755
  install_file calcifer-tools/calcifer-attach           /home/calcifer/.claude-container/calcifer-attach      calcifer calcifer 0755
  install_file calcifer-tools/calcifer-schedule         /home/calcifer/.claude-container/calcifer-schedule    calcifer calcifer 0755
  install_file calcifer-tools/calcifer-recent-dms       /home/calcifer/.claude-container/calcifer-recent-dms  calcifer calcifer 0755
  install_file calcifer-systemd/calcifer-discord.service /etc/systemd/system/calcifer-discord.service         root     root     0644
else
  echo "(skipping calcifer-* install table: calcifer user does not exist)"
fi

# --- decide what to restart ---
restart_bot=0
restart_sophie=0
restart_calcifer=0
reload_systemd=0
warn_dockerfile=0
warn_sophie_dockerfile=0
warn_calcifer_dockerfile=0
for path in "${CHANGED[@]:-}"; do
  case "$path" in
    /opt/claude-discord/bot.py)                     restart_bot=1 ;;
    /etc/systemd/system/claude-discord.service)     reload_systemd=1; restart_bot=1 ;;
    /home/claude/sandbox/Dockerfile)                warn_dockerfile=1 ;;
    /opt/sophie-discord/bot.py)                     restart_sophie=1 ;;
    /opt/sophie-discord/sched_time.py)              restart_sophie=1 ;;
    /etc/systemd/system/sophie-discord.service)     reload_systemd=1; restart_sophie=1 ;;
    /home/sophie/sandbox/Dockerfile)                warn_sophie_dockerfile=1 ;;
    /opt/calcifer-discord/bot.py)                   restart_calcifer=1 ;;
    /etc/systemd/system/calcifer-discord.service)   reload_systemd=1; restart_calcifer=1 ;;
    /home/calcifer/sandbox/Dockerfile)              warn_calcifer_dockerfile=1 ;;
  esac
done

if (( ${#CHANGED[@]} == 0 )); then
  echo "No changes — droplet already in sync with repo."
  exit 0
fi

echo
echo "Changed:"
printf '  %s\n' "${CHANGED[@]}"
echo

if (( DRY_RUN )); then
  echo "(dry-run; no actions taken)"
  (( reload_systemd ))           && echo "would: systemctl daemon-reload"
  (( restart_bot ))              && echo "would: systemctl restart claude-discord"
  (( restart_sophie ))           && echo "would: systemctl restart sophie-discord"
  (( restart_calcifer ))         && echo "would: systemctl restart calcifer-discord"
  (( warn_dockerfile ))          && echo "note: Howl Dockerfile changed — rebuild with: docker build -t claude-sandbox:latest /home/claude/sandbox"
  (( warn_sophie_dockerfile ))   && echo "note: Sophie Dockerfile changed — rebuild with: docker build -t sophie-sandbox:latest /home/sophie/sandbox"
  (( warn_calcifer_dockerfile )) && echo "note: Calcifer Dockerfile changed — rebuild with: docker build -t calcifer-sandbox:latest /home/calcifer/sandbox"
  exit 0
fi

if (( reload_systemd )); then
  echo "+ systemctl daemon-reload"
  systemctl daemon-reload
fi
if (( restart_bot )); then
  echo "+ systemctl restart claude-discord"
  systemctl restart claude-discord
  systemctl --no-pager status claude-discord | head -15
fi
if (( restart_sophie )); then
  echo "+ systemctl restart sophie-discord"
  systemctl restart sophie-discord
  systemctl --no-pager status sophie-discord | head -15
fi
if (( restart_calcifer )); then
  echo "+ systemctl restart calcifer-discord"
  systemctl restart calcifer-discord
  systemctl --no-pager status calcifer-discord | head -15
fi
if (( warn_dockerfile )); then
  echo
  echo "Howl Dockerfile changed. Rebuild the sandbox image when ready:"
  echo "    docker build -t claude-sandbox:latest /home/claude/sandbox"
fi
if (( warn_sophie_dockerfile )); then
  echo
  echo "Sophie Dockerfile changed. Rebuild the sandbox image when ready:"
  echo "    docker build -t sophie-sandbox:latest /home/sophie/sandbox"
fi
if (( warn_calcifer_dockerfile )); then
  echo
  echo "Calcifer Dockerfile changed. Rebuild the sandbox image when ready:"
  echo "    docker build -t calcifer-sandbox:latest /home/calcifer/sandbox"
fi
