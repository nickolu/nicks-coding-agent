FROM node:22-trixie-slim
# Trixie chosen to mirror Sophie's image (GLIBC 2.41 — keeps the household on
# one base distro). Calcifer doesn't need `gws`, so the OAuth keyring deps
# are optional, but kept for parity in case he ever needs Google Calendar
# integration (e.g. "post session reminders to my calendar").

RUN apt-get update && apt-get install -y --no-install-recommends \
    tmux curl ca-certificates python3 python3-pip python3-venv \
    jq ripgrep fd-find less nano vim-tiny tzdata \
    libsecret-1-0 gnome-keyring dbus-x11 \
    git \
    && rm -rf /var/lib/apt/lists/*

# Claude Code only. Calcifer reads /notebook/dnd, doesn't talk to Google APIs
# in v1, doesn't manage repos. Add `@googleworkspace/cli` here if/when he needs
# to read Nick's calendar to know session timing.
RUN npm install -g @anthropic-ai/claude-code

# Python deps for image generation (NPC portraits, location art, map sketches).
RUN pip install --no-cache-dir --break-system-packages openai google-genai

# Non-root user — UID 1002 to avoid collision with claude (1000) and sophie (1001).
RUN userdel -r node 2>/dev/null || true \
 && groupadd -g 1002 calcifer \
 && useradd -m -u 1002 -g 1002 -s /bin/bash calcifer

USER calcifer
WORKDIR /notebook
ENV HOME=/home/calcifer
ENV SHELL=/bin/bash
CMD ["bash"]
