---
name: grouping
description: Optimizes total cost and time across a whole body of work by grouping related tasks, ranking groups by value, and assigning each group both a model tier (local model via local-offload, then Claude Haiku, then Sonnet, then Opus) and an effort level (quick/standard/thorough) — escalating either only when a group genuinely needs it. Use whenever the user wants to work through a backlog, todo list, or the current session's outstanding tasks efficiently — "group these tasks", "organize my backlog", "prioritize and batch this work", "use cheaper models where possible", "what should I tackle first". Also use when the user wants a usage-budget-aware plan — "how much of this can I get done", "what's the fastest way through this", "show me my options given my usage limits". Runs standalone (ranking purely among Haiku/Sonnet/Opus when no local endpoint is configured), or hands mechanical groups to the local-offload skill when one is. For long batches, checkpoints progress via the handoff skill so the run survives context compaction or a closed session. Does not turn work into an unattended background loop — it executes a bounded batch in the current session and stops when the batch (or the user-approved portion of it) is done.
---

# Grouping: Batch Work by Value and Right-Sized Model

## Purpose

Most backlogs mix things that need real judgment with things that don't.
Running every item through the same (usually most expensive) model wastes
budget on the easy ones and risks under-serving the hard ones. This skill
fixes that by treating a backlog as batches, not a flat list:

1. **Group** related items so shared context (files, subsystem, task type)
   is loaded once per group, not once per item.
2. **Rank** groups by value — highest-impact / most-blocking work first.
3. **Right-size** each group on two axes: the cheapest *tier* that can
   produce a genuinely good result (local model → Haiku → Sonnet → Opus),
   and the *effort level* within that tier (quick → standard → thorough) —
   escalating either only when the group's content actually demands it.
4. **Execute** the groups in that order within the current session, without
   pausing for a check-in after every single group finishes.

You (the primary agent) stay accountable for the whole batch: you decide
the groupings, the ranking, and the tier assignments, and you review what
comes back from every dispatched group before considering it done.

## When to use this skill

Use it when there's a *body* of work, not a single task — a documented
backlog (a TODO file, a list of GitHub issues, a project board export), or
everything currently tracked in this session's task list. Don't use it for
one or two items; the grouping/ranking overhead isn't worth it below
roughly 4-5 discrete items.

## Operational steps

1. **Gather the work.** First check `.claude/handoffs/` for an existing
   checkpoint matching what the user's describing — if one exists, this is
   a resume, not a fresh start; hand off to the `handoff` skill's resume
   process rather than re-deriving the backlog. Otherwise collect the full
   item list from whichever source the user means:
   - The current session's tracked tasks (`TaskList`/`TaskGet` if this
     session has been using them).
   - A documented backlog the user points to (a file, an issue tracker
     query, a pasted list). Read it in full before grouping — don't group
     from a partial read.
   If the source is ambiguous, ask once rather than guessing which backlog
   they mean.

2. **Group for efficiency.** Cluster items that share context: same
   file(s)/component, same subsystem, same task *type* (e.g. "add tests
   across these five modules" is one group, not five). A group should be
   something one dispatched agent can execute with one coherent briefing.
   Don't force unrelated items into a group just to reduce the count —
   that just makes the briefing vague and the output worse.

3. **Prioritize groups by value.** Rank using whatever signal the backlog
   actually carries, in this order of preference:
   - Explicit signals already in the backlog (priority labels, deadlines,
     "blocks X" dependencies).
   - Blocking relationships you can infer (a group other groups depend on
     ships first).
   - User-stated goals for this session, if given.
   - As a last resort, cost of delay: bugs and broken-user-experience items
     before nice-to-haves; small high-leverage items before large
     speculative ones.
   If the value ranking is genuinely unclear (e.g. the user has competing
   goals and hasn't said which matters more), ask — don't silently guess at
   business priority.

4. **Assign each group both a tier and an effort level.** These are two
   separate axes — which model, and how hard it works within that model —
   don't collapse them into one decision.

   **Tier**: walk the ladder from cheapest and stop at the first tier
   that's genuinely sufficient — don't default everything to the same tier:
   - **Local model** (free, via the `local-offload` skill and its proxy) —
     if the group is mechanical/boilerplate by that skill's own criteria
     (fully-specified generation, repetitive refactors, no open design
     questions) *and* a local endpoint is actually configured and reachable.
   - **Haiku** — small, fully-specified, low-ambiguity, low-risk-of-error
     work that's a bit too varied or judgment-light for blind local
     generation but still doesn't need real reasoning: straightforward doc
     updates, mechanical renames/formatting across files, simple well-
     specified lookups or summaries.
   - **Sonnet** — the default for most real work: standard feature
     implementation, moderate refactors, ordinary bug fixes, test writing,
     most day-to-day coding. When in doubt between Sonnet and a cheaper
     tier, use Sonnet — under-provisioning quality-sensitive work is worse
     than the savings from guessing too low.
   - **Opus** — reserve for groups that actually need it: architectural or
     cross-cutting decisions, security-sensitive code, genuinely ambiguous
     requirements needing judgment calls, high-stakes or hard-to-reverse
     changes, or anything spanning many interacting systems.
   Note where the user's own Fable model might be a better fit than this
   ladder implies (e.g. conversational/creative-writing-flavored groups) —
   its cost/capability position isn't assumed here the way Haiku/Sonnet/
   Opus's is, so treat it as a deliberate per-group choice, not a default.

   **Effort**: independent of tier, set how much depth the dispatched work
   should apply — a Sonnet group doing a trivial rename needs a different
   effort level than a Sonnet group doing a moderate refactor with several
   edge cases:
   - **Quick** — single implementation pass, minimal exploration, no extra
     verification beyond confirming it matches the spec. For narrow,
     low-stakes, well-understood groups.
   - **Standard** — normal read-implement-verify cycle: check existing
     patterns before writing, review the diff, run relevant tests if any
     exist. The default for most groups.
   - **Thorough** — extra verification pass, explicit edge-case
     consideration, check for ripple effects on other parts of the
     codebase before finalizing. For high-stakes, ambiguous, or
     cross-cutting groups — usually (not always) paired with Opus, since
     the same signals that push tier up also push effort up.
   When dispatching, say the effort level explicitly in the brief (e.g. "this
   is a quick, narrow group — implement directly without extra exploration"
   vs. "this is a thorough group — verify edge cases and check for
   ripple effects before finalizing") so the assigned tier doesn't default
   to its own idea of how much work to do.

5. **For a batch of more than a handful of groups, write an initial
   checkpoint before executing anything** — use the `handoff` skill to
   create `.claude/handoffs/<slug>.md` with the full group ledger (every
   group, all `pending`), the goal, and the source backlog. This is what
   makes the batch survive context compaction or a closed session
   mid-run; skip it for small batches where losing state mid-run isn't a
   real risk.

6. **Dispatch each group and keep moving.** For a local-assigned group,
   follow `local-offload`'s process directly (architect the payload, route
   through the proxy, review output). For a Claude-tier group, use the
   `Agent` tool with `model` set to the assigned tier (`haiku`, `sonnet`,
   or `opus`) and a fully self-contained prompt — the sub-agent has no
   memory of this conversation, so include the actual file paths, the
   group's scope, and what "done" looks like for it, the same way you would
   brief any subagent. **Move directly to the next group once the current
   one is dispatched and its result reviewed — do not stop to ask the user
   whether to continue between groups.** Still apply the harness's normal
   safety rules inside each group (e.g. explicit-permission actions stay
   gated) — "don't stop between groups" means no unnecessary progress
   check-ins, not a license to skip real confirmations.

7. **Review before marking a group done, and update the checkpoint.** Read
   what came back from each dispatched group (local or Agent) and check it
   actually satisfies that group's scope before moving on or reporting it
   complete. Fix small deviations yourself rather than re-dispatching for
   minor issues. If a handoff checkpoint exists for this batch, update its
   ledger (status, outcome, next step) for this group before moving to the
   next one — not just at the end.

8. **Report at the end, not after every group.** Once the batch (or the
   user-approved slice of it) is done, give one summary: what was
   completed, at which tier and effort level, and anything that got
   escalated beyond its initial assignment (tier, effort, or both) and why.
   For local-routed groups, pull actual figures from the proxy's `GET
   /stats` (tokens, estimated $ saved) rather than just naming the tier —
   a real number is more convincing than "this ran locally." If a handoff
   checkpoint was in use, mark its status `done` (or delete it per that
   skill's cleanup guidance) rather than leaving it around as stale
   in-progress state.

## Usage-limit-aware assessment (optional)

If the user wants to see trade-offs before committing to a run — or asks
directly ("how much can I get done", "what's fastest", "show me my
options") — produce a scenario comparison instead of (or before) executing:

1. Get current usage/limit context: check whatever the environment exposes
   (e.g. an available usage-explanation skill or status surface) or ask the
   user directly for their remaining budget and reset timing if nothing
   else is available. Don't fabricate numbers.
2. Using the grouped, ranked backlog from steps 2-3 above, lay out three
   scenarios:
   - **Best output** — Opus (or the highest suitable tier) on every group
     the budget allows, in priority order. Report how many groups fit and
     which ones don't.
   - **Fastest output** — the quickest path through as much of the backlog
     as possible: lighter/faster tiers where sufficient, "quick" effort
     level throughout (skip the extra verification passes "standard"/
     "thorough" imply), and calling out which groups are independent
     enough to dispatch in parallel.
   - **Most groups completed** — the cheapest-sufficient-tier assignment
     from step 4, taken to its logical extreme: however many groups that
     buys you within budget.
3. Present the three as a compact comparison (groups completed, tier mix,
   rough budget used) and let the user pick — don't default to executing
   any one of them without the user choosing, since "best output" and
   "most groups completed" can lead to meaningfully different backlogs
   being finished.

## Working with local-offload

The two skills operate at different altitudes: `local-offload` decides
cloud-vs-local for a single mechanical sub-task; `grouping` decides, across
a whole backlog, which of {local, Haiku, Sonnet, Opus} each *group* of work
gets. When a group is mechanical enough to qualify for local offload,
delegate the tier decision for that group to `local-offload` rather than
duplicating its criteria here. `grouping` is also useful without
`local-offload` at all — e.g. no local endpoint is configured, and every
group just gets ranked among Haiku/Sonnet/Opus.

## Working with handoff

For batches big enough that losing track mid-run would actually hurt, use
the `handoff` skill to checkpoint the group ledger as described in steps 5
and 7 above — this is what lets a long run survive context compaction or a
closed session without the primary agent (or a future session) having to
reconstruct "which of N groups are done" from scratch. Small batches don't
need this; the overhead of maintaining a checkpoint isn't worth it below
roughly the same 4-5-item threshold as grouping itself.

## Working with roadmap

If the backlog being grouped is the breakdown of a `roadmap` initiative,
that's expected — `roadmap` stops at "here's what this initiative involves
and why it matters," `grouping` picks up from there to make it executable.
Report completion back in terms `roadmap` can use (what shipped) rather
than just tier/effort detail, so the initiative can be marked Shipped
without the user having to translate. `grouping` doesn't decide *what*
belongs on the roadmap or its priority relative to other initiatives —
that's `roadmap`'s call, one altitude up.

## Boundaries — what this skill does not do

- **Does not become an unattended background loop.** This skill executes a
  bounded batch within the current session when invoked. It does not
  schedule itself, wire itself into a recurring job, or set an open-ended
  goal to "eventually finish everything" unsupervised. If the user
  separately wants recurring/scheduled execution, that's their explicit
  decision each time, made through the normal scheduling tools — not
  something this skill initiates on its own.
- **Does not silently skip user review for genuinely ambiguous priority
  calls or risky actions.** "Don't stop between groups" is about avoiding
  unnecessary progress check-ins, not about bypassing the harness's
  explicit-permission action categories or asking when the value ranking
  is a real judgment call only the user can make.

## Notes

- Group sizes should stay small enough that one Agent dispatch can hold
  the whole group's context reliably — prefer more, smaller, coherent
  groups over few, sprawling ones.
- Tier assignments are estimates, not guarantees — if a Sonnet-assigned
  group's actual output is weak or the task turns out more ambiguous than
  it looked, escalate that specific group to Opus rather than accepting a
  poor result to save cost, and say so in the final report.
- This skill doesn't change the user's default model for the conversation
  itself — it only affects the tier used for each dispatched group's work.
