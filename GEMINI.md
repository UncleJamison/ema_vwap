## 🧠 Thinking Level & Reasoning

- High reasoning effort on all tasks.
- Workflow: 1. Analysis 2. Planning (via Beads) 3. Execution 4. Review 5. Response.
- Fix warnings/errors without silencing or commenting out code.

## Scope & Boundaries

- Local-only development in Google Antigravity IDE. All changes reversible.
- No Git commits, no pushes, no remote operations.
- Run pytest, ruff, lint, black, etc. frequently after each phase.
- Use `rg` for code search.

## Beads Task & Memory Management (Mandatory)

**Beads (`bd`) is the single source of truth** for tasks, issues, architecture, and persistent memory.

- **Session start**: Always run `bd ready --json` then `bd prime`.
- **Atomic Beads**: Create a bead for any task or refactor >2 minutes.
- **Onboarding**: Crawl directories first. Use `bd create --type forensic` for modules, functions, and interfaces.
- **Dependencies**: Link with `bd dep add`. Never treat modules in isolation.
- **Workflow**:
  - `bd ready` — list available work.
  - `bd create -t "Title" -d "Description"` — create beads (use direct flags).
  - `bd update <id> --claim` — claim work.
  - `bd update <id> --design "Chose Pattern A over B" --json' - update beads design decisions.
  - `bd close <id>` — complete work.
  - `bd block <id>` — for blocked tasks.
- **Hygiene**: `bd doctor --fix` when needed. Use `bd comment` for notes.
- **Persistence**: Important information MUST live in Beads or bead comments. Local chat context is volatile.

## Documentation & Planning

- Create phase outline files (`docs/<PHASE>-<SUBPHASE>-OUTLINE.md`) before development.
- Keep design simple, clean, and scalable.

## Autonomous Bug Fixing

- Fix bugs immediately using logs, errors, and failing tests.

## IDE Usage

- Leverage IDE for editing, running tests/linters, and `rg` searches.
- All actions local and reversible.

## Execution Workflow

1. Plan using Beads.
2. Self-critique plan 3-5 times for edge cases.
3. Execute one ready Bead at a time.
4. Run quality gates after each section.
5. Update/close Beads promptly.

**Session complete when quality gates pass and Beads status is current.**

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd help                                # Full help
bd ready                               # List available work
bd show <id>                           # View issue details
bd update <id> --claim                 # Claim work
bd close <id>                          # Complete work
bd remember "knowledge"                # Save knowledge
bd review                              # Review beads
bd doctor --fix                        # Fix issues
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
   bd dolt push
4. **Hand off** - Provide context for next session
