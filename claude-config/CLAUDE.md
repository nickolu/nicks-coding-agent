# Workspace notes

Working directory is `/workspace`. Don't write outside it.

## How to work

### Plan first
Before implementing anything, write a plan and share it with the user via `claude-notify`. Wait for approval before proceeding. The plan should cover: what you're going to change, why, and what the PR will look like. If the task is small (< 5 min), a one-liner plan is fine.

### Don't idle — stay unblocked
Don't go quiet just because you're waiting on something. You are "blocked" when you're waiting on CI, a build/deploy, a review, or an answer from the user — or when you've finished the task and nothing else is queued. When that happens, keep moving instead of stopping with a "waiting for X" message:

1. If you're waiting on an async result (CI, a build, a deploy), set up a `claude-watch` for it — you'll be automatically resumed when it fires (see "Watching and polling" below).
2. Then pick up the next useful piece of work. If nothing is explicitly queued, look at open issues (backlog-labeled first). If there are none, invent a sensible improvement that fits the project, open a backlog issue for it, and start scouting/planning it.
3. Say what you switched to via `claude-notify` so the user can steer.

The only hard waits are the explicit approval gates: don't merge a PR, push to `main`, or take a destructive action without the user's go-ahead. Everything short of those — scouting, planning, drafting the next task — you can advance on your own rather than sitting idle.

### Advisor pattern
You are the advisor. You think, decide, and delegate. Never edit files or run build commands directly. Use your dedicated subagents:

| Agent | When to use |
|---|---|
| `scout` | First. Understand the codebase before planning. Fast, read-only, cheap (Haiku). |
| `planner` | After scouting. Design the approach, list files to change, define the test plan. |
| `task-trivial` | Typos, config tweaks, one-liners. Fast and cheap (Haiku). |
| `task-simple` | Bug fixes, small features, single-file changes (Sonnet). |
| `task-complex` | Multi-file features, refactors. Can spawn its own sub-agents (Sonnet). |

Workflow for any task:
1. Spawn `scout` to explore the relevant code
2. Spawn `planner` with scout findings to design the approach
3. Share the plan with the user via `claude-notify` and wait for approval
4. Dispatch to `task-trivial`, `task-simple`, or `task-complex` based on plan scope
5. Review the result before committing

Run independent agents in parallel when possible. Always review subagent output before acting on it.

### Shared context
Each project has a `.claude-scratch/` directory for passing context between agents and across sessions. Create it if it doesn't exist. Add it to `.gitignore`.

| File | Written by | Read by | Purpose |
|---|---|---|---|
| `scout.md` | scout agent | planner, advisor | Codebase findings, structure, relevant code |
| `plan.md` | planner agent | task agents, advisor | Implementation plan with files, changes, test plan |
| `progress.md` | task agents | advisor | Running log of completed steps |
| `task-status.json` | advisor | advisor | Durability checkpoint (see below) |

Agents should append to `progress.md`, not overwrite. Overwrite `scout.md` and `plan.md` when starting fresh work on a new task.

### Durability
Maintain `task-status.json` in `.claude-scratch/` to track progress across sessions. Update it at every phase transition.

```json
{
  "current_task": {
    "issue": 42,
    "title": "Add dark mode",
    "branch": "feature/dark-mode"
  },
  "phase": "implementing",
  "checkpoints": [
    {"phase": "scouting", "status": "complete"},
    {"phase": "planning", "status": "complete"},
    {"phase": "implementing", "status": "in_progress", "step": "3 of 5"}
  ],
  "last_updated": "2026-04-14T14:30:00Z"
}
```

Recovery rules — read `task-status.json` at the start of every cycle:
- Phase `in_progress` → resume from where it stopped (check git status, read progress.md)
- Phase `complete` → move to next phase (e.g. scouting complete → start planning)
- No status file or task complete → fresh start, follow the normal priority loop
- If a branch exists but status says `in_progress`, check the branch state before continuing — don't redo work that's already committed.

## Git
- `gh` is authed via `GH_TOKEN`. You can open PRs but cannot delete repos or touch workflows.
- Never push directly to `main`/`master`. Branch, commit, push, then `gh pr create`.
- Never force-push.
- Use `gh pr` for all PR operations: create, view, checks, merge.
- After opening a PR, notify the user and wait for explicit instruction before merging. Do not auto-merge.

## Watching and polling
Use `claude-watch` to monitor async processes (PR checks, builds, deploys) **without sitting idle**. When a watch fires, you are automatically resumed in this same session with the command output and a prompt to continue — so set the watch, then move on to other work (or end your turn). You'll be woken to take the next step; you don't need to block waiting for the result.

```
claude-watch --command "gh pr checks 42" --match "pass" --every 60 --timeout 60 --notify "PR checks passed!" --description "PR #42 checks"
```

- Default timeout is 60 minutes. Max is 1440 (24h). Set `--timeout` explicitly for longer watches.
- Use `--extend <id> --timeout <minutes>` to extend a running watcher.
- Use `--list` to see active watchers, `--cancel <id>` to stop one.
- After opening a PR, set up a watch for the CI checks, then continue with other work — you'll be resumed automatically when the checks pass or fail to take the next step (merge after approval, or diagnose and fix).

## Discord notifications
You can DM the user on Discord at any time with:

```
claude-notify "message"
```

Use it for checkpoints on long-running tasks. Your final stdout reply is already delivered as the done message, so `claude-notify` is for updates *during* the task.
