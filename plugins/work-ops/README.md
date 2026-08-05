# work-ops

The work-orchestration half of
[hybrid-local-router](https://github.com/bishop07102001-code/hybrid-local-router) —
`roadmap`, `grouping`, and `handoff` only, no local LLM routing. No setup
required beyond installing the plugin — these are pure agent-behavior
skills. See the [main repo README](../../README.md) for full
documentation; this file is just the quick-start for this plugin
specifically.

## Skills

- **`roadmap`** — tracks strategic initiatives in `ROADMAP.md` using a
  Now/Next/Later format. Sequences by value and dependencies. Hands off to
  `grouping` once you decide to actually start an initiative.
- **`grouping`** — breaks a backlog (a roadmap initiative, your session's
  tracked tasks, or a documented to-do list) into value-ranked groups, then
  assigns each one a Claude tier (Haiku/Sonnet/Opus) and effort level
  (quick/standard/thorough) — the cheapest combination that can genuinely
  do it well. Works through the batch without stopping for a check-in
  after every group.
- **`handoff`** — checkpoints long-running or multi-session work to a
  structured file so it survives context compaction or a closed session.

## Usage

No install step — just ask, e.g. "add X to the roadmap", "group my
current task list and work through it efficiently", "checkpoint this
before I start a new conversation."

`grouping` will check for a local model via `local-offload` if you also
have [`local-llm-router`](../local-llm-router) or
[`hybrid-local-router`](../hybrid-local-router) installed, but works
completely standalone (ranking purely among Haiku/Sonnet/Opus) without it.
