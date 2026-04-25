#!/bin/bash
# PreToolUse hook tripwire for Claude Code.
# Reads a JSON payload on stdin describing the tool call.
# Exit 0 = allow; Exit 2 = block (stderr is shown to Claude).
# Also logs every decision to /home/claude/.claude/tripwire.log.

set -u
LOG=/home/claude/.claude/tripwire.log
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
    # Destructive rm
    echo "$cmd" | grep -Eq "rm[[:space:]]+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)[[:space:]]+(/|~|\\\$HOME|/\*)" && block "destructive rm of root/home"
    # Force push
    echo "$cmd" | grep -Eq "git[[:space:]]+push[[:space:]]+.*(--force|[[:space:]]-f[[:space:]]|[[:space:]]\+)" && block "git force push"
    # Direct push to main/master
    echo "$cmd" | grep -Eq "git[[:space:]]+push[[:space:]]+[^|&;]*[[:space:]](main|master)([[:space:]]|$)" && block "direct push to main/master — must go through PR"
    # Repo deletion / dangerous gh
    echo "$cmd" | grep -Eq "gh[[:space:]]+repo[[:space:]]+(delete|archive)" && block "gh repo delete/archive"
    echo "$cmd" | grep -Eq "gh[[:space:]]+repo[[:space:]]+edit.*--visibility" && block "gh repo visibility change"
    echo "$cmd" | grep -Eq "gh[[:space:]]+api.*-X[[:space:]]+DELETE" && block "gh api DELETE"
    # Curl/wget pipe to shell
    echo "$cmd" | grep -Eq "(curl|wget)[^|]*\|[[:space:]]*(sudo[[:space:]]+)?(bash|sh|zsh)" && block "curl|sh pattern"
    # Sudo
    echo "$cmd" | grep -Eq "(^|[[:space:]])sudo([[:space:]]|$)" && block "sudo invocation"
    # chmod 777
    echo "$cmd" | grep -Eq "chmod[[:space:]]+.*777" && block "chmod 777"
    # Writes to system paths
    echo "$cmd" | grep -Eq ">[[:space:]]*(/etc/|/root/|/var/|/usr/|/boot/|/sys/|/proc/)" && block "write to system path"
    # dd / mkfs / fork bomb
    echo "$cmd" | grep -Eq "(^|[[:space:]])dd[[:space:]]+if=" && block "dd command"
    echo "$cmd" | grep -Eq "mkfs" && block "mkfs"
    echo "$cmd" | grep -Eq ":\(\)\{.*:\|:.*\}" && block "fork bomb pattern"
    # Metadata IP
    echo "$cmd" | grep -Eq "169\.254\.169\.254" && block "cloud metadata endpoint"
    allow "bash_ok"
    ;;
  Write|Edit|MultiEdit|NotebookEdit)
    path=$(echo "$payload" | jq -r ".tool_input.file_path // .tool_input.notebook_path // empty")
    case "$path" in
      /workspace|/workspace/*) allow "write_in_workspace" ;;
      /home/claude/.claude|/home/claude/.claude/*) block "write to .claude config dir" ;;
      "") block "empty path" ;;
      *) block "write outside /workspace: $path" ;;
    esac
    ;;
  *)
    allow "other_tool"
    ;;
esac
