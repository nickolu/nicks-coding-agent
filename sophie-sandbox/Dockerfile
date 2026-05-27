FROM node:22-trixie-slim
# Trixie (Debian 13) ships GLIBC 2.41 — required by precompiled `gws` binary
# from @googleworkspace/cli (needs GLIBC 2.39+). Bookworm's 2.36 is too old.

# System deps — slimmer than Howl's image (no gh, no Playwright, no Chromium).
# Sophie writes notes and talks to Google APIs via the `gws` CLI; she never touches git/repos.
# libsecret-1-0 + gnome-keyring give @googleworkspace/cli somewhere to put its OS-keyring credentials.
# golang-go is needed by printingpress CLIs (pp-myfitnesspal et al.) — they install via `go install`.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tmux curl ca-certificates python3 python3-pip python3-venv \
    jq ripgrep fd-find less nano vim-tiny tzdata \
    libsecret-1-0 gnome-keyring dbus-x11 \
    golang-go git \
    && rm -rf /var/lib/apt/lists/*

# Claude Code + Google Workspace CLI (binary: `gws`, OAuth via `gws auth login`)
RUN npm install -g @anthropic-ai/claude-code @googleworkspace/cli

# Python deps for Sophie's tools (image generation via OpenAI + Google Gemini).
# --break-system-packages because Debian's PEP-668 marker on system Python; safe in a single-purpose container.
RUN pip install --no-cache-dir --break-system-packages openai google-genai

# printingpress CLIs (https://printingpress.dev) — agent-friendly tools that
# read MyFitnessPal logs, Wikipedia, Craigslist, etc. Installed via the
# orchestrator npm package which runs `go install` under the hood and drops
# binaries into $GOBIN. We set GOBIN to /usr/local/bin so the binaries land on
# the global PATH and run without per-user setup.
#
# Selection (picked by Nick 2026-05-10):
#   myfitnesspal — diet/calorie/macro logs (ties into the triglycerides health work)
#   wikipedia    — offline reference / lookups
#   craigslist   — local SD listings search
#
# Build-time auth: if the catalog or any module is private at install time,
# pass --build-arg GH_TOKEN=<token>. The token is NOT baked into the image.
ARG GH_TOKEN=
ENV GOBIN=/usr/local/bin
RUN bash -c 'set -e; \
    export GH_TOKEN="${GH_TOKEN:-}" GITHUB_TOKEN="${GH_TOKEN:-}"; \
    npx -y @mvanhorn/printing-press install myfitnesspal wikipedia craigslist \
      || echo "WARN: printingpress install failed — finish manually inside the container with: npx -y @mvanhorn/printing-press install myfitnesspal wikipedia craigslist"'

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
