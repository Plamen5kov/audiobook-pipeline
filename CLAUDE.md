# CLAUDE.md

Guidance for Claude Code and any other agent working in this repository.

**Read `AGENTS.md` first.** It holds the map: what the repo is, the four
codebases and which way they depend on each other, where things live, how to
add an analysis step, how to look inside a run, and the commands.

`ARCHITECTURE.md` is the reference for schemas, ports and the decision log.
`HOW-IT-WORKS.md` explains the project in plain language, for picking it back
up after time away.

The one rule worth repeating here, because breaking it is easy and the failure
is confusing: `core/` must stay importable without FastAPI, httpx or torch. The
corpus builder and the offline scripts import it with no stack running, and a
transport sneaking into `core` breaks both.
