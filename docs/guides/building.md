# Build & run (do not do this yourself)

> Part of the [docs wiki](../README.md). **This is a hard rule, not a suggestion.**

The user compiles and runs the project themselves. After writing or changing code:

- **Do NOT** proactively run builds or dev servers (`pnpm build`, `pnpm dev`, `uvicorn`, `python main.py`, `uv sync`, `docker exec ... psql`, etc.).
- **Do NOT** start background/long-running processes to "verify" a change.
- Instead, state what you changed and tell the user what to run and what to expect.
- Quick read-only checks that don't build or start a server are fine when they genuinely help (e.g. a one-off script the user asked for), but prefer describing the verification over performing it.

## Why

The consistent symptom of breaking this is a change that *looks* verified but wasn't: a `pnpm build` you ran successfully says nothing about the user's machine, and an un-built MCP `src/` edit is a silent no-op on their side (the server loads `build/index.js`). The user is the only one who can actually accept a change, so the hand-off is the deliverable.

## What to write instead

- Name the files changed.
- Give the exact command(s) to run.
- Say what to look for (log line, UI state, count) to know it worked.
- Flag anything the user must do first: `bump` MCP (`npm run build`), re-run `database/init.sh` (idempotent), restart the server (config / graph / tools).
