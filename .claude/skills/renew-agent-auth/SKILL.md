---
name: renew-agent-auth
description: Print the exact, filled-in steps to renew a claude-bridge agent's Claude setup-token when it 401s (Howl, Sophie, or Calcifer). Use when the user says an agent can't authenticate, or asks to renew/refresh/rotate an agent's token. Prints commands only — the user runs them (the browser auth step is theirs).
---

# Renew an agent's Claude auth token

The user invokes this as `/renew-agent-auth <agent>` (or asks in words). Your job
is to **print the exact command block, filled in for that agent** — do NOT run
the interactive renewal or write into the container yourself. The browser auth
and the paste are the user's to do. (This is deliberate: the user chose
"just print steps.")

## 1. Resolve the agent

Map the name (case-insensitive) to its user + launcher:

| agent | OS user | launcher |
|---|---|---|
| `howl` / `claude` | `claude` | `claude-sandbox` |
| `sophie` | `sophie` | `sophie-sandbox` |
| `calcifer` | `calcifer` | `calcifer-sandbox` |

If no agent is named, ask which one. If the name isn't in the table, say so and
list the three.

## 2. Confirm it actually needs renewing (optional but preferred)

Offer to run the read-only check first so you don't renew a healthy token:

```
ssh droplet /usr/local/bin/agent-auth-status
```

If that agent's row says `ok`, tell the user the token is fine and stop unless
they still want to rotate it. Only a `PROBLEM: live probe failed` row (or a
`token expires in Nd` warning) means renewal is due. Reproduce the 401 directly
if useful: `ssh droplet /usr/local/bin/<launcher> -p "say ok"`.

## 3. Print the renewal block, filled in

Substitute `<user>` and `<launcher>` from the table, then output this verbatim
for the user to run. Keep the two-pitfall note — it's the whole point.

> **Step 1 — open a shell in the container (needs a TTY, hence `-t`):**
> ```
> ssh -t droplet /usr/local/bin/<launcher> shell
> ```
>
> **Step 2 — start the token flow:**
> ```
> claude setup-token
> ```
> It opens a URL. The browser shows an **authorization code** — paste THAT back
> at the `claude setup-token` prompt (⚠️ not into any file). The CLI then prints:
> `Your OAuth token (valid for 1 year): sk-ant-oat01-…`
>
> **Step 3 — copy the `sk-ant-oat01-…` token** (⚠️ the whole thing — a wrapped
> terminal can truncate the copy), then, still inside the container:
> ```
> sed -i '/^CLAUDE_CODE_OAUTH_TOKEN=/d' ~/.claude/.env && \
>   read -rsp "paste token: " T && echo && \
>   case "$T" in \
>     sk-ant-oat01-*) \
>       printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\n' "$T" >> ~/.claude/.env && \
>       date -u -d '+1 year' +%Y-%m-%d > ~/.claude/.claude-token-expiry && \
>       echo "appended ok (${#T} chars)";; \
>     *) echo "REJECTED: that's not the token — look for the sk-ant-oat01- line";; \
>   esac
> ```
> The `case` guard rejects the paste unless it starts with `sk-ant-oat01-`, so
> the two classic mistakes (pasting the browser code, or a truncated token)
> can't slip through silently. Then `exit`.

Notes to include:
- `~/.claude` inside the container **is** the host's `/home/<user>/.claude-container`
  (bind-mounted), so this writes through to the host.
- **No systemd restart** — each agent call spawns a fresh `--rm` container that
  re-reads `.env`.
- The `.claude-token-expiry` sidecar it writes is what powers the proactive
  14-day expiry warning in the daily `agent-auth-check` cron.

## 4. Offer to verify after

Once the user says they've done it, offer to confirm:
```
ssh droplet /usr/local/bin/agent-auth-status
```
Expect that agent's row to read `ok` with `token exp` ~1 year out.

## Background

Full context (mechanisms, the access-token-vs-refresh-chain distinction, the two
401 message meanings) is in the repo `README.md` "Claude auth" section. Don't
re-explain it unless asked — this skill is the fast path.
