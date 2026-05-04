FROM node:22-trixie-slim
# Trixie (Debian 13) ships GLIBC 2.41 — required by precompiled `gws` binary
# from @googleworkspace/cli (needs GLIBC 2.39+). Bookworm's 2.36 is too old.

# System deps — slimmer than Howl's image (no gh, no Playwright, no Chromium).
# Sophie writes notes and talks to Google APIs via the `gws` CLI; she never touches git/repos.
# libsecret-1-0 + gnome-keyring give @googleworkspace/cli somewhere to put its OS-keyring credentials.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tmux curl ca-certificates python3 python3-pip python3-venv \
    jq ripgrep fd-find less nano vim-tiny \
    libsecret-1-0 gnome-keyring dbus-x11 \
    && rm -rf /var/lib/apt/lists/*

# Claude Code + Google Workspace CLI (binary: `gws`, OAuth via `gws auth login`)
RUN npm install -g @anthropic-ai/claude-code @googleworkspace/cli

# Non-root user — matches host `sophie` UID 1001.
# `node` user in the base image has UID 1000 which would collide with Howl's `claude` user
# if we ever shared a host volume, so we explicitly use 1001 here.
RUN userdel -r node 2>/dev/null || true \
 && groupadd -g 1001 sophie \
 && useradd -m -u 1001 -g 1001 -s /bin/bash sophie

USER sophie
WORKDIR /notebook
ENV HOME=/home/sophie
ENV SHELL=/bin/bash
CMD ["bash"]
