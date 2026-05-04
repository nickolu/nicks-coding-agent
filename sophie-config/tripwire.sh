#!/bin/bash
# PreToolUse hook tripwire for Sophie.
# Reads a JSON payload on stdin describing the tool call.
# Exit 0 = allow; Exit 2 = block (stderr is shown to Sophie).
# Logs every decision to /home/sophie/.claude/tripwire.log.
#
# Sophie is a personal manager — she reads/writes the notebook, talks to Google APIs
# via MCP, and DMs Nick on Discord. She never touches git, never runs sudo,
# never reaches outside /notebook on the filesystem.

set -u
LOG=/home/sophie/.claude/tripwire.log
mkdir -p "$(dirname "$LOG")"

payload=$(cat)
tool=$(echo "$payload" | jq -r ".tool_name // empty")
ts=$(date -Is)

block() {
    echo "[$ts] BLOCK tool=$tool reason=\"$1\"" >> "$LOG"
    echo "BLOCKED by tripwire: $1" >&2
    exit 2
}
allow() {
    echo "[$ts] ALLOW tool=$tool $1" >> "$LOG"
    exit 0
}

case "$tool" in
  Bash)
    cmd=$(echo "$payload" | jq -r ".tool_input.command // empty")
    # Sophie has no business with version control or repo hosts.
    echo "$cmd" | grep -Eq "(^|[[:space:]])(git|gh)([[:space:]]|$)" && block "git/gh not allowed for Sophie"
    # No privilege escalation.
    echo "$cmd" | grep -Eq "(^|[[:space:]])sudo([[:space:]]|$)" && block "sudo invocation"
    # Destructive rm of root/home.
    echo "$cmd" | grep -Eq "rm[[:space:]]+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)[[:space:]]+(/|~|\\\$HOME|/\*)" && block "destructive rm of root/home"
    # Curl/wget pipe to shell.
    echo "$cmd" | grep -Eq "(curl|wget)[^|]*\|[[:space:]]*(sudo[[:space:]]+)?(bash|sh|zsh)" && block "curl|sh pattern"
    # chmod 777.
    echo "$cmd" | grep -Eq "chmod[[:space:]]+.*777" && block "chmod 777"
    # Writes to system paths.
    echo "$cmd" | grep -Eq ">[[:space:]]*(/etc/|/root/|/var/|/usr/|/boot/|/sys/|/proc/)" && block "write to system path"
    # dd / mkfs / fork bomb.
    echo "$cmd" | grep -Eq "(^|[[:space:]])dd[[:space:]]+if=" && block "dd command"
    echo "$cmd" | grep -Eq "mkfs" && block "mkfs"
    echo "$cmd" | grep -Eq ":\(\)\{.*:\|:.*\}" && block "fork bomb pattern"
    # Cloud metadata endpoint.
    echo "$cmd" | grep -Eq "169\.254\.169\.254" && block "cloud metadata endpoint"
    # Try to read Howl's home (defense in depth — file perms also block this).
    echo "$cmd" | grep -Eq "/home/claude" && block "access to Howl's home"
    allow "bash_ok"
    ;;
  Write|Edit|MultiEdit|NotebookEdit)
    path=$(echo "$payload" | jq -r ".tool_input.file_path // .tool_input.notebook_path // empty")
    case "$path" in
      /notebook|/notebook/*) allow "write_in_notebook" ;;
      /home/sophie/.claude|/home/sophie/.claude/*) block "write to .claude config dir" ;;
      "") block "empty path" ;;
      *) block "write outside /notebook: $path" ;;
    esac
    ;;
  *)
    allow "other_tool"
    ;;
esac
