FROM node:22-bookworm-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git tmux curl ca-certificates gnupg python3 python3-pip python3-venv \
    jq ripgrep fd-find less nano vim-tiny openssh-client \
    && rm -rf /var/lib/apt/lists/*

# gh CLI
RUN mkdir -p -m 755 /etc/apt/keyrings \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
 && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update && apt-get install -y --no-install-recommends gh && rm -rf /var/lib/apt/lists/*

# Claude Code + Vercel CLI
# Vercel: deployment + project management. Auth via VERCEL_TOKEN env var in
# /home/claude/.claude-container/.env (generate at https://vercel.com/account/tokens).
# Avoid `vercel login` — its token cache lives outside the bind-mounted ~/.claude
# and would be lost between container runs.
RUN npm install -g @anthropic-ai/claude-code vercel

# Playwright MCP server + Chromium browser (shared location for non-root user)
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN npm install -g @playwright/mcp \
 && npx playwright install chromium --with-deps \
 && chmod -R o+rwx /opt/playwright

# Non-root user (matches host claude UID 1000)
RUN usermod -l claude node && groupmod -n claude node && usermod -d /home/claude -m claude || true

USER claude
WORKDIR /workspace
ENV HOME=/home/claude
ENV SHELL=/bin/bash
CMD ["bash"]
