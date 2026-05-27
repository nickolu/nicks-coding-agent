#!/bin/bash
# PreToolUse hook tripwire for Calcifer.
# Reads a JSON payload on stdin describing the tool call.
# Exit 0 = allow; Exit 2 = block (stderr is shown to Calcifer).
# Logs every decision to /home/calcifer/.claude/tripwire.log.
#
# Calcifer is a D&D dungeon-master assistant. He reads/writes /notebook/dnd,
# generates images and DMs Nick on Discord. He never touches git, never runs
# sudo, never reaches Sophie's or Howl's home dirs.

set -u
LOG=/home/calcifer/.claude/tripwire.log
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
    echo "$cmd" | grep -Eq "(^|[[:space:]])(git|gh)([[:space:]]|$)" && block "git/gh not allowed for Calcifer"
    echo "$cmd" | grep -Eq "(^|[[:space:]])sudo([[:space:]]|$)" && block "sudo invocation"
    echo "$cmd" | grep -Eq "rm[[:space:]]+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)[[:space:]]+(/|~|\\\$HOME|/\*)" && block "destructive rm of root/home"
    echo "$cmd" | grep -Eq "(curl|wget)[^|]*\|[[:space:]]*(sudo[[:space:]]+)?(bash|sh|zsh)" && block "curl|sh pattern"
    echo "$cmd" | grep -Eq "chmod[[:space:]]+.*777" && block "chmod 777"
    echo "$cmd" | grep -Eq ">[[:space:]]*(/etc/|/root/|/var/|/usr/|/boot/|/sys/|/proc/)" && block "write to system path"
    echo "$cmd" | grep -Eq "(^|[[:space:]])dd[[:space:]]+if=" && block "dd command"
    echo "$cmd" | grep -Eq "mkfs" && block "mkfs"
    echo "$cmd" | grep -Eq ":\(\)\{.*:\|:.*\}" && block "fork bomb pattern"
    echo "$cmd" | grep -Eq "169\.254\.169\.254" && block "cloud metadata endpoint"
    echo "$cmd" | grep -Eq "/home/(claude|sophie)" && block "access to another agent's home"
    allow "bash_ok"
    ;;
  Write|Edit|MultiEdit|NotebookEdit)
    path=$(echo "$payload" | jq -r ".tool_input.file_path // .tool_input.notebook_path // empty")
    case "$path" in
      /notebook|/notebook/*) allow "write_in_notebook" ;;
      # Auto-memory carve-out, same as Sophie.
      /home/calcifer/.claude/projects/*/memory|/home/calcifer/.claude/projects/*/memory/*) allow "write_in_memory" ;;
      /home/calcifer/.claude|/home/calcifer/.claude/*) block "write to .claude config dir" ;;
      "") block "empty path" ;;
      *) block "write outside /notebook: $path" ;;
    esac
    ;;
  *)
    allow "other_tool"
    ;;
esac
