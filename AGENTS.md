# AGENTS.md

This project follows the mission, workflow rules, and conventions in `CLAUDE.md` — read it first.

## Checkpoint convention

When the user says **"checkpoint"** (optionally `checkpoint: <message>`): stage all changes
(`git add -A`), commit with a concise message summarizing the diff (or the user's message),
and push to `origin main`. The word "checkpoint" is the user's explicit, standing
authorization for these three git operations — no re-confirmation needed. All other git
mutations (rebase, reset, force-push, branching, history edits) still require explicit
confirmation each time.
