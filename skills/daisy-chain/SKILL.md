---
name: daisy-chain
description: Adds the current device to an existing Exo cluster with as little friction as possible — diagnoses everything needed up front (Exo install, mlx, Metal Toolchain, Python version compatibility) in one pass, asks for confirmation exactly once covering the whole install plan, then executes end-to-end and verifies the device actually joined the cluster. Runs locally, on the device being added — invoke it by opening a Claude Code session (or SSH session you're already in) on that device and asking to "add this machine to the cluster" or "join the Exo cluster" or "daisy chain this device". Deliberately does not reach out to or install anything on other devices remotely — see local-offload's Troubleshooting for why (running installers on machines you can't directly watch has a meaningfully worse failure mode than doing it in front of a human).
---

# Daisy Chain: Low-Friction Cluster Join

## Purpose

Exo already clusters devices on the same network with zero configuration —
two Exo instances on the same LAN discover each other and elect a master
automatically. The actual friction in "connect all your devices" is never
the clustering protocol, it's the per-device install: missing `mlx`, a
missing Metal Toolchain component, a Python version needing a compatibility
shim, orphaned processes from a previous attempt. Every one of those is a
known, previously-solved problem (see local-offload's Troubleshooting
section for the full incident). This skill's entire value is front-loading
that diagnosis into one pass instead of discovering each problem serially
the way a first attempt inevitably does.

**The friction reduction is in batching, not in skipping consent.** One
accurate plan, one confirmation covering everything in it, then an
uninterrupted run — not zero confirmations. Installing software and
downloading files still require an explicit yes, every time this skill is
used; what disappears is *repeated* asking for each individual step.

## When to use this skill

Run it **on the device being added** — open a Claude Code session there
(or use an SSH session you're already logged into yourself), and ask to
join it to the cluster. This skill does not run from one machine and reach
out to configure others; see the frontmatter and Boundaries below for why.

## Operational steps

1. **Diagnose everything in one pass before proposing anything.** Don't
   fix-as-you-discover — that's what turns a 5-minute join into a
   multi-hour debugging session. Check, in order:
   - Is Exo already installed? (`command -v exo`, or check for its
     project directory if you know the convention this user follows)
   - Is `mlx` importable in Exo's actual venv? (`<exo-venv>/bin/python -c
     "import mlx.core"` — not just any `python3`, the exact interpreter
     Exo's process will use; a mismatch here is exactly what cost the most
     time previously)
   - Python version — if the venv is on Python < 3.10, pydantic needs the
     `eval_type_backport` shim or Exo's own code will crash with a
     `TypeError` on startup.
   - Does the Metal compiler actually work, not just "is Xcode
     installed"? `xcrun -sdk macosx metal --version` — if it fails with
     "missing Metal Toolchain," that's a ~700MB separate download via
     `xcodebuild -downloadComponent MetalToolchain`, distinct from Xcode
     itself and easy to miss.
   - Is anything already listening on Exo's port from a previous partial
     attempt? (`lsof -nP -iTCP:52415 -sTCP:LISTEN`) — stale processes from
     an earlier failed attempt can silently absorb requests meant for a
     freshly-started one.
   - Hardware check: free RAM/disk (same assessment as local-offload's
     bootstrap flow), so the confirmation in step 2 can also mention what
     capacity this device is bringing to the cluster.

2. **Present the full plan once, then ask once.** Consolidate everything
   step 1 found into a single clear summary before touching anything:
   what's missing, what each fix requires (install/download and
   approximate size), and what's already fine and will be skipped. Get one
   explicit yes covering the whole plan — don't re-confirm each
   sub-install once the plan's been approved. If step 1 found nothing
   missing at all, say so and skip straight to step 4.

3. **Execute the approved plan end-to-end without stopping for further
   confirmation.** A few things learned the hard way, worth encoding
   directly rather than rediscovering:
   - Activate any virtualenv and run dependent commands **within the same
     shell invocation** — activation does not reliably persist across
     separate tool calls, and re-launching a process later "in a new
     shell" without re-activating is exactly how a stale system-Python
     process ends up bound to the port instead of the intended one.
   - If a Metal Toolchain download is needed, do it before attempting the
     `mlx` build — attempting the build first just produces a wall of
     `cannot execute tool 'metal'` errors that look unrelated to the
     actual cause.
   - After installing, verify the fix actually landed (e.g. re-run the
     `import mlx.core` check) before declaring success — don't infer
     success just because the install command exited 0.

4. **Start (or restart) Exo and verify it actually joined the cluster** —
   don't just confirm the process is running, confirm it found the
   *existing* cluster rather than electing itself a new standalone master.
   Exo's own startup log says this directly (a `discovered: Some(...)`
   line, and whether it elected itself master or found an existing one).
   If it started as its own isolated master instead of joining, that's a
   network-reachability problem (different subnet, firewall, mDNS
   blocked) — say so plainly rather than reporting success on a technicality.

5. **Report clearly**: what was already fine, what got installed, and
   confirmation the device is now part of the existing cluster (not a
   second isolated one). Point at the model catalog / current cluster
   capacity now that this device's RAM/disk is contributing to it.

## Boundaries — what this skill does not do

- **Does not reach out to or install anything on other devices.** It runs
  on the device being added, invoked by a human physically at (or
  remotely logged into) that device. A version that SSHes out from one
  session and configures a fleet of other machines unattended has a
  meaningfully worse failure mode — a bad step affects every device in the
  chain at once, on machines nobody's watching in real time to catch it —
  and was deliberately scoped out rather than defaulted into.
- **Does not reduce friction by skipping or batching-away consent for the
  install/download itself.** Only the *number of prompts* is reduced (one
  full-plan confirmation instead of several partial ones) — never the
  requirement that installing software or downloading files gets an
  explicit yes.

## Notes

- This skill is Exo-specific — Exo is the backend in this plugin that
  actually clusters across devices. Ollama/LM Studio/vLLM/llama.cpp are
  single-machine backends; there's no "join" operation for them, so this
  skill doesn't apply there (each of those devices would instead be its
  own independent `local-offload` backend, not part of one pooled
  cluster).
- If this is the *first* device rather than one joining an existing
  cluster, that's `local-offload`'s "Bootstrapping a local backend from
  scratch" flow instead — this skill assumes a cluster already exists
  somewhere to join.
