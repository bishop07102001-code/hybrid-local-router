---
name: local-offload
description: Optimizes token usage and wall-clock time by splitting work between a cloud architect and a local worker. Use this skill whenever a task involves heavy file generation, boilerplate scaffolding, repetitive syntax refactoring, mechanical code translation, or any sub-task whose instructions can be fully specified up front. The skill keeps architectural reasoning, planning, and review on the primary cloud model, and redirects the mechanical execution payload to a local LLM backend (Exo, Ollama, LM Studio, vLLM, or llama.cpp — auto-detected) via `scripts/router_proxy.py`, tracking usage so savings are a real number (`GET /stats`). If no local backend exists yet, this skill also handles getting one set up — assessing the machine's hardware and recommending an easy-onramp backend and starter model, only installing anything with explicit confirmation — so it doubles as an approachable way to get hands-on with local models in the first place. Use it too for requests like "help me get started with local models", "is my machine set up for local LLMs", "what local model should I use". Do not use this skill for tasks that require ongoing judgment, ambiguous requirements, security-sensitive decisions, or cross-file architectural trade-offs — those must stay on the cloud model.
---

# Local Offload: Hybrid Cloud/Local Task Router

## Purpose

This skill reduces cloud token spend and turnaround time by treating every
non-trivial coding task as two phases:

1. **Architect phase** (cloud / primary agent, i.e. you): understand intent,
   design the approach, and produce a fully-specified worker payload.
2. **Worker phase** (local cluster): execute mechanical, well-specified work
   — file generation, boilerplate, syntax refactors — on a local
   OpenAI-compatible endpoint instead of burning cloud tokens.

You (the primary agent) always remain the architect. You never hand off
ambiguous or judgment-heavy work. You only offload work you could fully
specify yourself but would rather not spend cloud tokens generating
mechanically.

## When to use this skill

Offload candidates (route to local):
- Generating multiple boilerplate files from a known pattern (e.g. CRUD
  handlers, DTOs, test scaffolds, config files).
- Mechanical syntax refactors (e.g. renaming, converting callbacks to
  async/await, reformatting, updating import styles) across many files.
- Repetitive code generation where the pattern is fixed and only inputs vary.
- Expanding a fully-specified spec into verbose source (e.g. writing out a
  full REST client from an OpenAPI spec you already parsed).

Keep on cloud (do not offload):
- Architectural decisions, API design, technology choices.
- Anything requiring judgment about ambiguous or conflicting requirements.
- Security-sensitive logic (auth, crypto, permissions, input validation
  design).
- Final review, correctness verification, and integration of worker output.
- Tasks too small to be worth the round trip (a single one-line edit).

## Operational steps

1. **Classify the task.** Before writing any code yourself, decide whether
   the current sub-task is architectural (keep) or mechanical/repetitive
   (offload candidate). If unsure, keep it on cloud — offloading is an
   optimization, not a requirement.

2. **Architect the payload.** For offload candidates, do the thinking
   yourself first: decide the exact files to produce, the pattern each
   should follow, naming conventions, imports, and any project-specific
   constraints (language version, framework, style guide). The local model
   should not need to make any design decisions — it only needs to execute.

3. **Check local cluster availability.** The proxy handles this itself
   internally, but check before a batch of offload work so you know what
   you're working with:

   ```bash
   curl -s http://localhost:8787/health
   ```

   - `local_cluster_reachable: false` + `backend: "generic"` means no
     local backend was found *at all* — not cold, not warming, genuinely
     nothing there. This is different from every other unavailable state
     and worth handling differently: see "Bootstrapping a local backend
     from scratch" below instead of just falling back silently.
   - Otherwise, look at `ready_local_model` (usable right now, if any) and
     `provisioning_in_progress` (a model is loading in the background —
     expect the *next* offload call to fall back to cloud once more, then
     succeed locally after that).

4. **Build the worker request.** Construct a standard OpenAI-compatible
   chat completion payload containing:
   - A `model` field — for an Exo backend this is just a placeholder; the
     proxy resolves it to whatever's actually running (or auto-provisions
     one), so don't spend effort picking a specific model id yourself.
   - A system message stating the exact output format expected (e.g. "Return
     only the complete file contents, no explanation, no markdown fences").
   - A user message with the fully-specified instructions from step 2:
     target file path, pattern/template to follow, and all concrete inputs
     (names, fields, types, etc). Leave no open design questions in this
     payload.

5. **Route the call through the proxy.** Send the request to
   `scripts/router_proxy.py` (run as a local FastAPI service) rather than
   directly to the cloud API. Example:

   ```bash
   python3 scripts/router_proxy.py &
   curl -s http://localhost:8787/v1/chat/completions \
     -H "Content-Type: application/json" \
     -d '{
           "model": "local-worker",
           "messages": [
             {"role": "system", "content": "Return only complete file contents."},
             {"role": "user", "content": "Generate a Pydantic model named User with fields id:int, email:str, created_at:datetime."}
           ],
           "task_type": "boilerplate"
         }'
   ```

   The proxy inspects `task_type` (and, if absent, heuristically classifies
   the payload) and, for offload candidates, manages the whole local model
   lifecycle itself against an Exo backend:
   - If a model is already loaded and ready, it's used as-is (with request
     parameters like `enable_thinking` adjusted for mechanical work).
   - If nothing is loaded, the proxy checks free RAM/disk, picks the best
     model that fits (preferring code-capable ones), and starts provisioning
     it **in the background** — this specific request falls back
     immediately rather than waiting, and subsequent requests will find the
     new model ready (large models can take several minutes to load and
     compile for the first time; this cost is paid once per model, not once
     per request).
   - If nothing local can be provisioned at all, it falls back to a
     configured cloud upstream, or otherwise returns the request untouched
     so you can send it to the cloud model instead.

6. **Handle local-cluster failure or unavailability gracefully.** Two cases,
   same response — fall back to generating the file(s) yourself on the
   cloud model for this task, and continue without blocking:
   - The proxy reports the local cluster is unreachable (HTTP 502 with
     `"local_cluster_offline"`).
   - The proxy responds with `"pass_through": true` and a `router_reason`
     mentioning auto-provisioning just started — this is expected on the
     first offload attempt against a cold local cluster. Don't retry the
     same request against local; just do it on cloud this once. Later
     offload calls in the same session should succeed locally once
     provisioning finishes.

7. **Review and integrate worker output.** Never write local-model output
   directly to disk unreviewed. Read the returned content, check it matches
   the pattern and constraints you specified in step 2, fix any deviations,
   and only then write it to the target file(s) using your normal file
   tools.

8. **Report savings.** When you finish an offloaded batch, briefly note to
   the user how many files/tasks were routed locally vs. kept on cloud.
   For a real number rather than a count, check `GET /stats` on the proxy
   — it tracks tokens routed locally and an estimated $ saved.

## Bootstrapping a local backend from scratch

If `GET /health` shows nothing detected at all (`local_cluster_reachable:
false`, `backend: "generic"`), that's not "cold, will warm up" — it means
there's no local LLM server running on this machine for the proxy to talk
to. Don't just silently fall back to cloud every time; offer to help,
since getting a local backend running is usually a five-minute problem and
this skill's entire value proposition disappears without one.

If the user mentions an *existing* Exo cluster elsewhere on their network
(another device already running Exo) rather than starting from zero, this
is a join, not a fresh install — hand off to the `daisy-chain` skill
instead of the from-scratch flow below.

**Offer once per session, not every request.** The first time you hit this
state in a conversation, mention it and explain the value briefly — no
per-token cost for the mechanical work this skill handles, works offline,
and it's a genuinely easy way to get hands-on with what local models can
and can't do well. If the user declines or doesn't engage, proceed on
cloud for the rest of the session and don't bring it up again unprompted.

If they want to set one up:

1. **Assess the hardware** — OS, RAM, disk space, GPU/accelerator (Apple
   Silicon, NVIDIA, none):
   ```bash
   uname -s                                  # OS
   # macOS:
   sysctl -n hw.memsize hw.model
   # Linux:
   free -h; lspci | grep -i nvidia
   df -h ~
   ```

2. **Recommend one easy default, not all five options.** For someone
   getting started, **Ollama** is almost always the right first
   recommendation — single install, cross-platform (macOS/Linux/Windows),
   auto-manages model loading, no separate config to understand. Only
   suggest something else if there's a clear reason to (e.g. they mention
   wanting a GUI → LM Studio; they mention a multi-GPU rig and want max
   throughput → vLLM). Match the *model* recommendation to what you found
   in step 1 — a small, well-known, genuinely useful model (a ~3-8B class
   instruct or coder model) that will actually run well on their hardware,
   not the biggest thing that technically fits. Oversized-for-the-hardware
   is the single most common way someone's first local-model experience
   goes badly (painfully slow, looks broken) and turns them off the whole
   idea.

3. **Get explicit confirmation before installing or downloading anything**
   — state exactly what you're about to run, what it downloads, and
   roughly how large (Ollama itself, plus the specific model and its
   approximate size), and wait for a clear yes. This isn't optional or a
   style preference: installing software and downloading files both
   require explicit permission every time, regardless of how eager the
   user seemed to get started.

4. **Install and pull the starter model**, e.g. for Ollama:
   ```bash
   curl -fsSL https://ollama.com/install.sh | sh   # macOS/Linux; see ollama.com for Windows
   ollama pull <recommended-model>
   ```

5. **Verify and continue.** Check `GET /health` again to confirm the proxy
   now detects the new backend, then proceed with the original offload
   task using it. Point out `GET /stats` exists so they can watch savings
   accumulate from here — that's the "how it's useful" half of
   familiarizing themselves with it, not just the install.

## Notes

- The local endpoint is assumed OpenAI-compatible. The proxy auto-detects
  Exo, Ollama, LM Studio, vLLM, and llama.cpp; against any other backend it
  just forwards your requested `model` field as-is and trusts the local
  server to already have something loaded. Auto-discovery/selection from a
  catalog only applies to Exo/Ollama/LM Studio (vLLM and llama.cpp are
  commonly deployed with one model fixed at launch — nothing to pick).
  Ollama/LM Studio selection only considers models already downloaded — it
  won't trigger a new download on its own; Exo's equivalent
  (`LOCAL_ALLOW_AUTO_DOWNLOAD`) defaults to the same off-by-default policy,
  so a brand-new multi-gigabyte download never happens silently on any
  backend. Thinking suppression doesn't work on LM Studio (a current LM
  Studio API limitation, not this proxy's doing — see README
  Troubleshooting).
- This skill never sends secrets, credentials, or proprietary data to the
  local endpoint beyond what the task itself already requires; the local
  cluster is assumed to be trusted infrastructure on the user's own network.
- This skill does not modify the user's actual chat/model routing — it only
  provides a convention and a proxy script the primary agent can choose to
  use when it judges a sub-task to be a good offload candidate.
- Auto-provisioning picks the largest model that fits available RAM/disk
  (with headroom), preferring code-capable models. It will not always pick
  the same model twice — if free memory changes between provisioning
  events, a different model may be chosen. This is expected.
