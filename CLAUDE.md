@AGENTS.md

# CLAUDE.md

This file is the Claude Code entry point. Its first line imports AGENTS.md, the
single source of truth for all tool-neutral rules. Do not duplicate any
AGENTS.md rule here.

## Claude Code bindings

### Style precedence

- AGENTS.md and this file override the global `~/.claude/CLAUDE.md` where they
  conflict.
- The global rule to ask before assuming is narrowed here. Under the autonomy
  model in AGENTS.md, decide and record. Ask only at a stop condition.
- Global brevity rules apply to chat. They do not apply to docs, decision
  records, pull request descriptions, commit bodies or reports.

### Session start

1. Read `docs/design.md` in full.
2. Read the "Current state" section of AGENTS.md and the newest files in
   `docs/decisions/`.
3. Check for open pull requests and for a live run before starting new work.
4. If the work will touch Azure and the owner has not named the subscription in
   this session, that is stop condition 1.

### Subagents

- `azure-docs-verifier` is the binding for the documentation-verification role
  in AGENTS.md's truth rules. Use it for every Azure capability, SKU, limit,
  API version or preview claim before it goes into code or docs. Its
  Microsoft Learn tools come from `.mcp.json`.

### Settings

- `.claude/settings.json` is shared and checked in. It pre-approves the build,
  test, Git, GitHub and Azure command families so a session does not stall on
  prompts. It denies force-push, repo deletion, resource group deletion and
  reads of secret-bearing files.
- A permission prompt for anything else is a signal. Check it against the hard
  safety rules before approving a wider rule.

## Project mechanics (Claude Code only)

Append Claude-Code-specific mechanics here as they are learned. Tool-neutral
mechanics go in AGENTS.md.
