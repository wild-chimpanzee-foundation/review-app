---
name: parallel-work
description: Delegate independent tasks to headless Pi workers using Git worktrees and persistent zmx sessions. Use when work can safely proceed in parallel.
---

# Parallel work with Pi, Git worktrees, and zmx

The current Pi process coordinates shell-launched workers; no subagent extension is required. Give each worker its own worktree, branch, and unique zmx session. Worktrees separate files, not permissions, and share Git metadata.

## Plan and prepare

- Inspect repository instructions and status; preserve the user's changes. Parallelize only independent, worthwhile tasks, normally with 2–4 workers. Serialize overlapping files or shared interfaces.
- Give each worker a bounded task, context, allowed paths, acceptance criteria, and verification commands. Forbid commits, merges, pushes, further workers, and edits outside its worktree unless explicitly requested.
- Require a final report of changed/new files, checks/results, and blockers. Workers needing clarification should stop and report rather than await interactive input.
- Check branch/path ownership before creating a worktree; never silently reuse or remove existing ones:

```bash
git worktree add -b agent/<task-name> ../<repo>-<task-name> <base-ref>
```

- Worktrees contain committed files only. Supply relevant instructions in the prompt; copy only reviewed, required local configuration without overwriting files or copying credentials. Install dependencies per repository instructions; keep mutable environments, outputs, databases, and ports separate.
- Record each worker's base commit, pre-launch status/diff and required untracked files, setup-only files, and artifact paths. Uncommitted user changes are not inherited: arrange their inclusion explicitly. If task paths start dirty, agree on a baseline-relative transfer method before launch.

## Launch

Check `pi`, `bash`, and `zmx` availability and installed CLI help. If zmx is missing, ask before substituting another mechanism. Check `zmx list` to avoid session collisions.

Create a prompt and launcher outside each worktree. Include the task and worker rules above: children do not inherit the coordinator's conversation. `zmx run` sends shell text, not preserved argument boundaries; put complex commands in a script instead of a quoted `bash -lc` argument.

Example `/absolute/path/to/worker-a.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /absolute/path/to/worktree
exec pi --model openai-codex/gpt-6-luna --thinking high \
  --no-approve --print @/absolute/path/to/task-a-prompt.txt
```

Choose trust explicitly: `--no-approve` skips protected project settings/resources; replace with `--approve` only after reviewing and trusting them. Headless mode cannot prompt for trust. Context files such as `AGENTS.md` load independently. Explicit model/thinking flags avoid relying on copied or trusted settings.

```bash
zmx run agent-task-a -d bash /absolute/path/to/worker-a.sh
```

Use shell-safe launcher paths (no spaces/metacharacters), or preserve quoting in the transmitted command text. Launch one worker per session: commands within a session run sequentially. Use `run`, not nested `attach`, which can switch the caller's terminal.

## Monitor and review

- Track session names, branches, worktree paths, and baselines. Wait with `zmx wait <session...>`; inspect `zmx history <session> | tail -100` or follow with `zmx tail <session>`.
- `--print` shows the final response, not tool progress; silence does not imply a stall. For progress, use `--mode json` instead and inspect JSONL error/aborted events, not just exit status.
- After workers stop, inspect reports, exit status, status/diffs against the baseline, and untracked/new files. Run relevant checks in each worktree; completion alone is not success.

## Integrate without commits

Uncommitted edits cannot be merged from worker branches. Transfer reviewed patches one at a time, without staging or committing in the coordinator checkout.

If task paths were clean before launch and `HEAD` still matches the recorded base, run in the worker worktree:

```bash
# Skip if there are no new files; intent-to-add includes them in the diff.
git add -N -- <reviewed-new-files>
git diff --binary HEAD -- <reviewed-task-paths> > /absolute/path/to/task-a.patch
```

Use an unused patch path outside the worktree. Inspect the patch; exclude setup-only files, supplied user changes, and unrelated edits. For initially dirty task paths, use the agreed baseline-relative method instead.

Check coordinator status/diffs for overlap with existing user changes, then run:

```bash
git apply --check /absolute/path/to/task-a.patch
git apply /absolute/path/to/task-a.patch
```

Apply only after successful preflight and confirming user edits are preserved. Resolve conflicts deliberately; ask when intent is unclear. Never reset, blanket-stash, overwrite, or force-apply. Review the result before the next patch, then run the relevant full test/lint/type-check suite and report outcomes. Commit-based integration requires an explicit user request.

## Cleanup

Leave worktrees, branches, sessions, and prompt/launcher/patch files unless cleanup was requested. Verify contents before removal; patch transfer leaves source worktrees dirty, so successful integration does not justify force-removal.
