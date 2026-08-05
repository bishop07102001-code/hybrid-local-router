---
name: handoff
description: Manages context-window limitations across long or multi-session work by writing and reading a structured, human-readable checkpoint file that captures the goal, a per-group/per-item status ledger, key decisions made along the way, and the next concrete step. Use when a body of work is too big to finish in one context window, when the user says things like "let's continue this later", "checkpoint this", "I'm going to start a new conversation", "prepare a handoff", "where did we leave off", or "summarize progress so I can resume". Invoked proactively by the grouping skill during long multi-group runs so a batch survives context compaction or a session restart without losing track of what's done. Writes to .claude/handoffs/ in the current project — durable across sessions, not just within one conversation's context.
---

# Handoff: Checkpoint State Across Context and Session Boundaries

## Purpose

Two different things can erase task-tracking state mid-work: the harness's
own context compaction (which summarizes older turns — generally fine, but
opaque, and can blur specific structured state like "which of 15 groups are
done and what exactly did group 6 decide"), and simply starting a new
conversation. Both are common during long batches — `grouping` processing
many groups in one sitting is exactly the scenario where this bites.

This skill's job is a *deliberate, structured, durable* checkpoint that
survives both: written to a file in the project (not just conversation
memory), readable by any future session regardless of whether it's a
continuation or a fresh `claude` invocation.

## When to use this skill

- The user asks directly: "checkpoint this", "let's continue later",
  "prepare a handoff", "where did we leave off", "I'm starting a new
  conversation, summarize progress."
- Proactively, during a `grouping` run of more than a handful of groups —
  write/update a checkpoint after each group completes so an interruption
  (compaction, closed session, hit a usage limit) doesn't lose the ledger.
- At the start of a session, if a handoff file exists in `.claude/handoffs/`
  that looks relevant to what the user's asking about — check before
  assuming you're starting cold.

## Where handoffs live

`.claude/handoffs/<slug>.md` in the current project, where `<slug>` is a
short kebab-case identifier for the backlog/goal (e.g.
`api-v2-migration.md`, `q3-backlog.md`) — stable across the life of that
piece of work, not timestamped per checkpoint. **Update the same file in
place** as work progresses; don't create a new file per checkpoint, or
"where did we leave off" stops having one clear answer. If genuinely
distinct bodies of work are in flight at once, they get separate slugs.

## Writing a handoff

1. **Pick or confirm the slug.** If continuing an existing handoff, use its
   file. If starting one, derive a short slug from the goal and check
   `.claude/handoffs/` doesn't already have something similar (avoid
   accidental duplicates for the same backlog).

2. **Write the full structure**, even on the first checkpoint — don't grow
   it incrementally in a way that leaves early sections thin:

   ```markdown
   # Handoff: <goal, one line>

   Last updated: <ISO timestamp>
   Status: in-progress | blocked | done

   ## Goal
   <what this body of work is trying to accomplish, and why, in 2-4
   sentences — enough that someone with zero prior context understands the
   point, not just the task list>

   ## Source
   <where the backlog came from — a file path, "this session's task list",
   an issue tracker query — so a resuming session can re-derive the full
   item list if needed>

   ## Group ledger
   | # | Description | Tier / Effort | Status | Outcome |
   |---|---|---|---|---|
   | 1 | <short description> | Sonnet / standard | done | <one line: what happened, files touched> |
   | 2 | <short description> | local / — | in-progress | <started at X, expected shortly> |
   | 3 | <short description> | Opus / thorough | pending | — |

   ## Key decisions
   <bullet list of things that took discussion or judgment to establish —
   the nuance generic compaction is most likely to blur. Only decisions
   that would change future groups' work if forgotten; not routine choices.>

   ## Blockers / open questions
   <anything waiting on the user, or a decision only they can make>

   ## Next step
   <the single concrete next action — which group, what to do first>
   ```

3. **Update in place** as groups complete — rewrite the status/outcome
   cells and the "Next step" line rather than appending a log. The file
   should always read as "current state," not a history of every change.

4. **Keep it concise.** This is a resumption aid, not a full transcript —
   the group ledger's "Outcome" column is one line, not a paragraph. If a
   group's full output matters for later reference, point to where it
   actually lives (the file it touched, a PR, a report) rather than
   copying it into the handoff.

## Resuming from a handoff

1. **Read the relevant handoff file in full** before doing anything else —
   don't start re-deriving the backlog from scratch if a checkpoint exists.
2. **Verify before trusting.** State goes stale — a file the handoff
   references may have changed independently since the checkpoint was
   written. Spot-check a couple of "done" items against actual current
   state (does the file really reflect what the ledger claims happened)
   before treating the whole ledger as reliable, especially if the handoff
   is old.
3. **Confirm briefly with the user** if anything is ambiguous — multiple
   candidate handoff files, an unclear "Status: blocked" reason, or a gap
   between what the handoff says and what you observe — rather than
   silently picking an interpretation for a real judgment call.
4. **Continue from "Next step."** Don't restart already-`done` groups.

## Working with grouping

`grouping`'s own operational steps call into this skill: at the start of a
run spanning more than a handful of groups, write an initial handoff with
the full ledger (all groups, all `pending`); after each group completes,
update that same file's status/outcome/next-step rather than waiting until
the end. This means an interrupted run — context compaction, a closed
session, hitting a usage limit mid-batch — always has a same-file, current
checkpoint to resume from, not a stale or partial one.

This skill is also useful standalone, for any long single-thread task that
isn't going through `grouping` at all — a big migration, a multi-day
debugging effort, anything where "what have we established so far" is
worth writing down deliberately rather than trusting compaction alone.

## Cleaning up

Once a handoff's status is `done` and there's no reason to expect picking
it back up, delete the file (or ask the user if you're not sure it's truly
finished) rather than leaving completed handoffs to accumulate and
confuse future "which handoff is current" lookups.

## Notes

- This does not replace the harness's own automatic context compaction —
  it's a deliberate, structured supplement for exactly the case compaction
  handles worst: many discrete tracked items whose individual status
  matters.
- Handoff files are plain markdown in the project directory — they're
  visible to the user directly (and committable to version control, if the
  user wants that history), not a hidden mechanism.
