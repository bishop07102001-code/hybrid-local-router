---
name: roadmap
description: Maintains a strategic Now/Next/Later roadmap of initiatives in ROADMAP.md — separate from and above grouping's altitude, which executes a concrete backlog right now. Use when the user wants to add something to the roadmap, review or resequence it, plan out a longer horizon (a quarter, a release, "what should we build next"), or check status/progress across initiatives. When the user decides to actually start an initiative, this skill hands off to grouping to break it into an executable backlog — it does not execute work itself. Triggers: "add this to the roadmap", "what's on the roadmap", "what should we prioritize next", "update the roadmap", "plan the next quarter/release", "how are we tracking against the roadmap". Does not auto-start work on initiatives without being asked, and does not turn roadmap review into an unattended execution loop.
---

# Roadmap: Strategic Initiative Tracking

## Purpose

`grouping` answers "what should I work on right now, in this session."
This skill answers a different question: "what are we building over the
next weeks/months, and in what order." Conflating the two produces either
a roadmap cluttered with task-level detail, or a backlog vague enough that
nobody can actually execute it. Keep them separate:

- **Roadmap** (this skill): initiatives — features, migrations, themes —
  each big enough to eventually become its own backlog. Tracked in
  `ROADMAP.md`, reviewed and resequenced occasionally, not executed
  directly.
- **Backlog / groups** (`grouping`): the concrete, executable breakdown of
  *one* initiative (or any ad hoc set of tasks), worked through now.

This skill's job stops at "here's what we're building and in what order."
Starting the work is a deliberate handoff to `grouping`, not something this
skill does on its own.

## When to use this skill

- Adding, reviewing, or resequencing initiatives — not individual tasks.
  If what the user's describing is small enough to just do this session,
  that's `grouping`'s job, not this skill's.
- Planning a horizon longer than "right now" — a quarter, a release, "what
  should we tackle after the current thing."
- Checking status/progress across initiatives, or reporting what shipped.

## Where the roadmap lives

`ROADMAP.md` in the project root — deliberately not tucked into `.claude/`
the way `handoff`'s checkpoints are, since a roadmap is normally something
the whole team should see and is worth committing to version control.

## Structure

```markdown
# Roadmap

Last updated: <date>

## Now
- **<Initiative>** — <one-line description>. Value: <why this, why now>.
  Depends on: <other initiative, or none>.

## Next
- **<Initiative>** — <description>. Value: <...>. Depends on: <...>.

## Later
- **<Initiative>** — <description>. Value: <...>.

## Shipped
- **<Initiative>** — shipped <date>. <one-line outcome/impact if known>.

## Cut
- **<Initiative>** — cut <date>. <one-line reason, so it doesn't get
  re-proposed without knowing why it was dropped before>.
```

Deliberately dateless for Now/Next/Later (that's the point of the format —
it communicates sequence and confidence without a false-precision ship
date), but timestamp entries once they move to Shipped or Cut, since those
are facts, not projections.

## Operational steps

1. **Read the existing roadmap in full** before adding or changing
   anything — don't append blindly without seeing what's already there
   (duplicate or conflicting initiatives are the most common roadmap-doc
   failure).

2. **Adding an initiative**: get enough detail to make it useful later —
   what it is, why it matters (value), what it depends on. An initiative
   without a stated "why" is hard to prioritize honestly later; push back
   gently if the user gives you a title with no rationale. Place it in
   Now/Next/Later based on their stated intent, or ask if genuinely
   unstated — don't silently assume urgency.

3. **Resequencing**: when asked to reprioritize, or when adding a new
   initiative changes the picture, apply the same value-ranking approach
   `grouping` uses for tasks, one level up — explicit priority signals
   first, then dependency order (an initiative blocking others moves up),
   then user-stated goals, then cost-of-delay as a last resort. If two
   initiatives compete for the same "Now" slot and the trade-off is a real
   judgment call (limited team capacity, conflicting strategic bets), ask
   rather than picking for the user.

4. **Starting an initiative**: when the user decides to actually begin
   work on something in Now, that's the handoff point to `grouping` — help
   break the initiative into concrete backlog items (this is still
   planning, not execution) and then either point the user at `grouping`
   or invoke it directly if they want to proceed immediately. Don't start
   executing sub-tasks yourself from within this skill.

5. **Tracking status**: when work on an initiative completes (you'll
   typically learn this from a `grouping` run's final report, or the user
   telling you directly), move it to Shipped with the date and a one-line
   outcome. If something is abandoned, move it to Cut with the reason —
   don't just delete entries; the "why we didn't do this" record has value
   later when the same idea resurfaces.

6. **Status reports**: when asked "how are we tracking," summarize what
   shipped since the last check-in, what's actively in Now, and anything
   that's stalled or slipped — don't just re-paste the roadmap file
   verbatim, synthesize it into a short answer to the actual question.

## Working with grouping and handoff

Altitude, in order: **roadmap** (what to build, over what horizon) →
**grouping** (breaking one initiative or ad hoc request into an executable,
value-ranked, right-sized backlog) → **handoff** (checkpointing a long
`grouping` run so it survives context/session boundaries). Each skill
hands off downward when the conversation moves from strategy to
execution; none of them skip a level — don't let `roadmap` start dispatching
`Agent` calls directly, and don't let `grouping` redefine what's on the
roadmap.

## Boundaries — what this skill does not do

- **Does not execute initiatives.** Breaking one down into a backlog and
  hand it to `grouping` is as far as this skill goes on its own.
- **Does not auto-advance the roadmap unsupervised.** Moving something
  from Next to Now, or deciding an initiative is worth starting, is the
  user's call unless they've explicitly delegated that judgment for this
  session — this skill surfaces the trade-off, it doesn't resolve strategic
  priority calls on its own.
- **Does not fabricate ship dates.** The Now/Next/Later format exists
  specifically so this skill isn't pressured into inventing false-precision
  timelines it has no basis for.

## Notes

- Keep initiative descriptions at the "what and why," not "how" — the how
  is `grouping`'s job when the initiative actually starts.
- If `ROADMAP.md` doesn't exist yet and the user wants to start one, create
  it with just a Now section from whatever they describe rather than
  demanding a fully populated Next/Later up front — a roadmap with one
  honest entry beats an empty template.
