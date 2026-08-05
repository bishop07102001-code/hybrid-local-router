# claude-efficiency-suite

A Claude Code plugin marketplace with five skills spanning strategy to
execution — available as **one combined plugin, or two focused ones**,
your choice:

| Plugin | Contains | Needs local hardware? |
|---|---|---|
| **`claude-efficiency-suite`** | All five skills | Only for `local-offload`/`daisy-chain`'s features — the rest work without it |
| **`local-llm-router`** | `local-offload`, `daisy-chain` | Yes, to actually route locally (but it'll help you set one up) |
| **`work-ops`** | `roadmap`, `grouping`, `handoff` | No |

```
/plugin marketplace add bishop07102001-code/claude-efficiency-suite

/plugin install claude-efficiency-suite@claude-efficiency-suite   # everything
/plugin install local-llm-router@claude-efficiency-suite       # just local routing
/plugin install work-ops@claude-efficiency-suite                # just planning/execution
```

The five skills:

- **`roadmap`** — tracks strategic initiatives in `ROADMAP.md` using a
  Now/Next/Later format (no false-precision ship dates). Sequences by
  value and dependencies. Hands off to `grouping` once the user decides to
  actually start an initiative — it plans, it doesn't execute.
- **`grouping`** — takes a backlog (a roadmap initiative's breakdown, the
  current session's tracked tasks, or a documented to-do list) and batches
  it into value-ranked groups, then dispatches each group to the cheapest
  resource that can do it well: the local model, then Haiku, then Sonnet,
  then Opus, at a matching effort level (quick/standard/thorough) —
  escalating either only when a group genuinely needs it. Works standalone
  or hands mechanical groups to `local-offload`.
- **`local-offload`** — routes heavy file generation, boilerplate creation,
  and mechanical syntax refactoring to a **local LLM** (Exo, Ollama, LM
  Studio, vLLM, or llama.cpp — auto-detected), while keeping high-level
  architectural planning, judgment calls, and review on your primary cloud
  model (Claude). Tracks usage so the savings are a real number, not a
  vague claim — see `GET /stats`. If no local backend exists yet, it also
  assesses your hardware and helps you set one up (with confirmation
  before installing anything) — an on-ramp for getting familiar with
  local models, not just a router for people who already have one.
- **`daisy-chain`** — adds a device to an *existing* Exo cluster with as
  little friction as possible: diagnoses everything needed (Exo, `mlx`,
  Metal Toolchain, Python compatibility) in one pass, asks once with the
  full plan rather than prompting per step, then executes and verifies the
  device actually joined. Runs on the device being added — no remote/SSH
  orchestration across a fleet.
- **`handoff`** — checkpoints long-running or multi-session work to a
  structured file so it survives context compaction or a closed session.
  Used standalone, or invoked automatically by `grouping` during long
  multi-group batches.

In every case the primary agent stays the architect. It only hands off
work it could already fully specify itself — a dispatched worker never
makes design decisions, it just executes.

---

# Skill: roadmap

No separate service to run — described in full in
[`plugins/claude-efficiency-suite/skills/roadmap/SKILL.md`](plugins/claude-efficiency-suite/skills/roadmap/SKILL.md) (also in `plugins/work-ops/`).

**What it does**: maintains `ROADMAP.md` in your project root as a
Now/Next/Later list of initiatives — deliberately dateless for anything not
yet shipped, since false-precision ship dates are worse than no date.
Sequences by value and dependencies the same way `grouping` ranks tasks,
one altitude up. Tracks Shipped (with date + outcome) and Cut (with
reason, so a dropped idea doesn't get silently re-proposed) as permanent
record, not deleted history.

**What it deliberately doesn't do**: execute anything itself. When an
initiative moves from planning to "let's build this," that's a handoff to
`grouping` to break it into an executable backlog — `roadmap` stops at
*what* and *why*, `grouping` picks up *how* and *right-sized-by-whom*.

**Usage**: "add X to the roadmap", "what's next after the current thing",
"how are we tracking", "let's start on the initiative we planned for Now."
Works standalone (it's just a markdown file and a sequencing convention),
and composes with `grouping`/`handoff` for anything on it big enough to
need them.

---

# Skill: local-offload

## How it works

```
 ┌──────────────┐        classify task        ┌──────────────────────┐
 │  Primary      │ ───────────────────────────▶│  router_proxy.py      │
 │  cloud agent  │                              │  (FastAPI, port 8787) │
 │  (Claude)     │◀─────────────────────────────│                        │
 └──────────────┘   reviewed worker output     └──────────┬────────────┘
                                                            │
                                    boilerplate / refactor  │  everything else
                                                            ▼
                                          ┌───────────────────────────────┐
                                          │  Local OpenAI-compatible node   │
                                          │  e.g. Exo @ localhost:52415/v1  │
                                          └───────────────────────────────┘
```

1. The primary agent decides whether a sub-task is mechanical (boilerplate,
   scaffolding, repetitive refactors) or architectural (design, judgment,
   security-sensitive).
2. Mechanical sub-tasks are fully specified by the primary agent, then sent
   as a standard OpenAI-format chat completion request to the proxy
   (`scripts/router_proxy.py`, wherever you installed it — see
   Installation above).
3. The proxy classifies the request (via an explicit `task_type` field or a
   keyword heuristic). For offload candidates against Exo, Ollama, LM
   Studio, vLLM, or llama.cpp, it also manages the model lifecycle itself,
   logging usage as it goes — see "Automatic model discovery
   & provisioning" below — before forwarding to your local cluster using
   API key `sk-local`. Anything that isn't an offload candidate is returned
   untouched (or sent to an optional cloud upstream) so the caller can send
   it to the cloud model instead.
4. If the local cluster is unreachable, or no local model is ready and none
   can be provisioned, the proxy returns a clear error/pass-through instead
   of hanging, so the primary agent can fall back to doing the work itself
   on cloud.
5. The primary agent always reviews and integrates worker output before
   writing it to disk — nothing from the local model is trusted blindly.

**Don't have a local backend yet?** The agent handles that too — if
`GET /health` shows nothing detected at all, it assesses your hardware,
recommends an easy-onramp backend and a hardware-matched starter model
(Ollama by default — single install, cross-platform), and only installs
anything after explicit confirmation of what's being downloaded. See
`local-offload/SKILL.md`'s "Bootstrapping a local backend from
scratch" for the full flow. This only offers once per session — if you
decline, it falls back to cloud for the rest of the conversation without
asking again.

## Automatic model discovery & provisioning

The proxy auto-detects which local backend it's talking to (`GET /health`
→ `backend`: `exo` / `ollama` / `lmstudio` / `vllm` / `llamacpp` / `generic`)
by probing `LOCAL_CONTROL_URL` once per process, and doesn't require you to
know or specify which model is loaded for any of the first five:

1. **Checks for a running/warm model.** If one is already loaded and has
   completed at least one successful call, the proxy uses it as-is —
   overriding whatever placeholder `model` string the caller sent — and
   adjusts request parameters for mechanical work (thinking suppression;
   field names differ per backend — see the table below).
2. **If nothing is running, assesses capacity and picks a model** (Exo /
   Ollama / LM Studio only — vLLM and llama.cpp are commonly deployed with
   a single model fixed at launch, so there's nothing to pick: whatever
   `/v1/models` reports is already loaded and warm before the server even
   accepts connections).
   - **Exo**: checks real free RAM (`psutil`, not just OS "free" pages) and
     free disk, then picks the best model from Exo's catalog that fits
     (code-capable first, then largest that fits) and downloads/places it.
   - **Ollama / LM Studio**: checks free RAM only, and picks from
     **already-downloaded** models only (`ollama list` / LM Studio's model
     manager) — code-named first, then largest that fits. Neither will
     trigger a new download on its own; that's a multi-gigabyte action this
     plugin won't start without you.
3. **Provisions/warms it in the background, never blocking the triggering
   request.** A freshly-loaded model's first inference can pay a one-time
   cold-start cost that scales with model size (minutes, not seconds, for
   large Exo models) — no timeout is short enough to make waiting
   synchronously reasonable. The request that triggered this falls back
   immediately (to `CLOUD_UPSTREAM_URL` if configured, otherwise
   `pass_through`); once the background task finishes, **subsequent**
   offload requests use the model and are fast.
4. **If nothing usable can be found at all** (not enough capacity, or the
   control API can't be reached), the request falls back the same way —
   cloud upstream, or pass-through to the primary agent.

| Backend | Thinking suppression | Selection scope |
|---|---|---|
| Exo | `enable_thinking` + `reasoning_effort` | Full catalog, auto-downloads |
| Ollama | `think` | Already-pulled models only |
| LM Studio | *(none — see Troubleshooting)* | Already-downloaded models only |
| vLLM | `chat_template_kwargs.enable_thinking` + `reasoning_effort` | N/A — one fixed model |
| llama.cpp | `chat_template_kwargs.enable_thinking` + `reasoning_effort` | N/A — one fixed model |

Check `GET /health` at any time — `backend`, `ready_local_model` (usable
right now), `loaded_but_unwarmed_model` (loaded but not yet proven fast),
and `provisioning_in_progress` (model + elapsed seconds, if a background
provision/warmup is underway).

Against any other OpenAI-compatible server (text-generation-webui, ...),
the proxy skips all of this and simply forwards your requested `model`
field as-is — that server manages its own loaded model outside this
proxy's control.

## What's included

This repo is a **marketplace** (`.claude-plugin/marketplace.json` at the
root, listing all three plugins below) rather than a single plugin itself —
each plugin lives in its own self-contained subdirectory under `plugins/`
with a complete copy of whatever skills it includes (plugins can't
reference files outside their own directory, so `local-offload` and
`daisy-chain`'s files are duplicated between `claude-efficiency-suite` and
`local-llm-router`, and `roadmap`/`grouping`/`handoff`'s between
`claude-efficiency-suite` and `work-ops`). Edits to shared skills need to be
applied to both copies.

```
claude-efficiency-suite/                        (marketplace root)
├── .claude-plugin/
│   └── marketplace.json                    # lists the 3 plugins below
├── plugins/
│   ├── claude-efficiency-suite/                # full suite
│   │   ├── .claude-plugin/plugin.json
│   │   ├── skills/
│   │   │   ├── roadmap/SKILL.md
│   │   │   ├── grouping/SKILL.md
│   │   │   ├── local-offload/SKILL.md
│   │   │   ├── daisy-chain/SKILL.md
│   │   │   └── handoff/SKILL.md
│   │   ├── scripts/router_proxy.py         # FastAPI proxy for local routing
│   │   └── install.sh                      # one-command dependency setup
│   ├── local-llm-router/                   # local-offload + daisy-chain only
│   │   ├── .claude-plugin/plugin.json
│   │   ├── skills/{local-offload,daisy-chain}/SKILL.md
│   │   ├── scripts/router_proxy.py
│   │   └── install.sh
│   └── work-ops/                           # roadmap + grouping + handoff only
│       ├── .claude-plugin/plugin.json
│       └── skills/{roadmap,grouping,handoff}/SKILL.md
├── LICENSE
└── README.md
```

## Prerequisites

- `work-ops` alone needs nothing beyond Claude Code itself.
- `claude-efficiency-suite` / `local-llm-router` additionally need Python
  3.10+, and a local OpenAI-compatible inference server (Exo, Ollama, LM
  Studio, vLLM, llama.cpp, or anything else OpenAI-compatible — see
  [`local-offload`](#skill-local-offload)'s bootstrap flow if you don't
  have one yet).

## Installation

### 1. Install the plugin(s) you want

**From the marketplace** (recommended) — pick one, or more than one:

```
/plugin marketplace add bishop07102001-code/claude-efficiency-suite
/plugin install claude-efficiency-suite@claude-efficiency-suite   # everything
/plugin install local-llm-router@claude-efficiency-suite       # local routing only
/plugin install work-ops@claude-efficiency-suite                # planning/execution only
```

**Or manually**, if you're developing locally — copy whichever plugin
directory (or directories) you want into your plugins directory:

```bash
cp -r plugins/claude-efficiency-suite ~/.claude/plugins/claude-efficiency-suite
# or: cp -r plugins/local-llm-router ~/.claude/plugins/local-llm-router
# or: cp -r plugins/work-ops ~/.claude/plugins/work-ops
```

Either way, restart Claude Code (or reload plugins) so the skills are
picked up — you should see them listed among available skills.

**If you installed `work-ops` only**, that's it — no further setup. The
rest of this section is for `claude-efficiency-suite` / `local-llm-router`.

### 2. Install the proxy's Python dependencies

```bash
cd plugins/claude-efficiency-suite   # or plugins/local-llm-router
./install.sh
```

This creates `.venv`, installs `fastapi`/`uvicorn`/`httpx`/`psutil`, and
(on Python < 3.10) the `eval_type_backport` shim pydantic needs. Equivalent
manual steps, if you'd rather not run the script:

```bash
cd plugins/claude-efficiency-suite   # or plugins/local-llm-router
python3 -m venv .venv
source .venv/bin/activate
pip install fastapi "uvicorn[standard]" httpx psutil
```

### 3. Start your local LLM cluster

Bring up Exo (or your local server of choice) so it's listening at
`http://localhost:52415/v1`. For Exo:

```bash
exo
```

Verify it's serving an OpenAI-compatible API:

```bash
curl -s http://localhost:52415/v1/models
```

Don't have a local server set up yet? `local-offload` (in either
`claude-efficiency-suite` or `local-llm-router`) will assess your hardware and
help you install one — see its "Bootstrapping a local backend from
scratch" flow, described below.

### 4. Start the router proxy

```bash
python3 scripts/router_proxy.py
```

(from inside the plugin directory you set up in step 2). By default this
binds to `0.0.0.0:8787`. Check its health endpoint:

```bash
curl -s http://localhost:8787/health | python3 -m json.tool
```

Expected output when the local cluster is up:

```json
{
    "status": "ok",
    "local_endpoint": "http://localhost:52415/v1",
    "local_cluster_reachable": true,
    "cloud_upstream_configured": false
}
```

Check accumulated savings at any time:

```bash
curl -s http://localhost:8787/stats | python3 -m json.tool
```

## Usage

Once the proxy is running, the `local-offload` skill instructs the primary
agent to route fully-specified, mechanical sub-tasks through it instead of
generating that content itself on the cloud model. You can also call the
proxy directly to test routing behavior:

**Offload a boilerplate request (goes to the local cluster):**

```bash
curl -s http://localhost:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
        "model": "local-worker",
        "task_type": "boilerplate",
        "messages": [
          {"role": "system", "content": "Return only complete file contents, no explanation."},
          {"role": "user", "content": "Generate a Pydantic model named User with fields id:int, email:str, created_at:datetime."}
        ]
      }'
```

**A request that requires judgment (passed through, not offloaded):**

```bash
curl -s http://localhost:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
        "model": "local-worker",
        "messages": [
          {"role": "user", "content": "Which caching architecture should we use for this service and why?"}
        ]
      }'
```

This returns `"pass_through": true` since no `CLOUD_UPSTREAM_URL` is
configured by default — the caller (the primary agent) is expected to
handle it directly on the cloud model rather than have the proxy forward it
anywhere.

### Explicit routing hints

Set `task_type` on any request to skip the heuristic classifier:

| `task_type` value | Routing |
|---|---|
| `boilerplate`, `refactor`, `codegen`, `scaffold`, `syntax` | Local cluster |
| `architecture`, `planning`, `review`, `design`, `reasoning` | Cloud (or pass-through) |
| *(omitted)* | Heuristic keyword classification on message content |

### Optional: forwarding non-offload requests to a cloud endpoint

By default, requests that aren't offload candidates are returned to the
caller with `pass_through: true` rather than being forwarded anywhere. If
you'd rather have the proxy itself forward those to a cloud OpenAI-compatible
endpoint, set:

```bash
export CLOUD_UPSTREAM_URL="https://api.anthropic.com/v1"
export CLOUD_UPSTREAM_API_KEY="sk-ant-..."
```

## Configuration reference

All configuration is via environment variables, read at proxy startup:

| Variable | Default | Description |
|---|---|---|
| `LOCAL_ENDPOINT` | `http://localhost:52415/v1` | Base URL of your local OpenAI-compatible server |
| `LOCAL_API_KEY` | `sk-local` | API key sent to the local server |
| `LOCAL_SUPPRESS_THINKING` | `true` | Send a backend-appropriate "disable reasoning" field (`enable_thinking`/`reasoning_effort` for Exo, `think` for Ollama; no-op for LM Studio — see Troubleshooting) and `temperature=0.2` on local calls. Set `false` for backends that reject unrecognized fields |
| `LOCAL_CONTROL_URL` | *(derived from `LOCAL_ENDPOINT`)* | Base URL of the local backend's control/management API (Exo's instance API, or Ollama's native API), not the `/v1` completions surface. Used to auto-detect Exo vs. Ollama vs. neither |
| `AUTO_PROVISION_MODEL` | `true` | Auto-place a model instance on Exo when none is running |
| `LOCAL_ALLOW_AUTO_DOWNLOAD` | `false` | Whether Exo may pick and place a **new** model from its catalog (can trigger a multi-gigabyte download). Off by default, matching Ollama/LM Studio's never-auto-download behavior — Exo will still use or warm up an already-loaded model regardless of this setting |
| `MODEL_RAM_HEADROOM_MB` | `2048` | Free RAM to leave unused (beyond a candidate model's size) when picking a model to auto-provision |
| `MODEL_DISK_HEADROOM_MB` | `5120` | Free disk space to leave unused when picking a model to auto-provision |
| `MODEL_CACHE_PATH` | `~` | Filesystem path used to check free disk space |
| `MODEL_PROVISION_TIMEOUT_SECONDS` | `600` | Max time the background task waits for a placed instance to become ready |
| `MODEL_PROVISION_POLL_INTERVAL_SECONDS` | `5` | How often the background task polls cluster state while waiting |
| `MODEL_WARMUP_TIMEOUT_SECONDS` | `600` | Max time the background task waits for the post-load warmup call to finish |
| `USAGE_DB_PATH` | `~/.claude-efficiency-suite/usage.sqlite3` | SQLite file the proxy logs routed requests to, for `GET /stats` |
| `CLOUD_INPUT_COST_PER_MTOK` / `CLOUD_OUTPUT_COST_PER_MTOK` | `3.0` / `15.0` | USD per million input/output tokens, used only to *estimate* cloud cost avoided in `GET /stats`. Placeholders — set to your actual plan's effective rate for a meaningful number |
| `CLOUD_UPSTREAM_URL` | *(unset)* | Optional cloud endpoint for non-offload requests, and the fallback tier when no local model can be used at all |
| `CLOUD_UPSTREAM_API_KEY` | *(empty)* | API key for `CLOUD_UPSTREAM_URL`, if set |
| `ROUTER_HOST` | `0.0.0.0` | Host the proxy binds to |
| `ROUTER_PORT` | `8787` | Port the proxy binds to |
| `ROUTER_TIMEOUT_SECONDS` | `120` | Timeout for local/cloud completion requests |
| `ROUTER_HEALTH_TIMEOUT_SECONDS` | `3` | Timeout for `/health` reachability checks |
| `ROUTER_LOG_LEVEL` | `INFO` | Python logging level |

## Local discovery notes

- Point `LOCAL_ENDPOINT` at whatever host/port your cluster actually
  listens on — if Exo (or another node) runs on a different machine on your
  LAN, use its address, e.g. `http://192.168.1.42:52415/v1`.
- The `/health` endpoint is the fastest way to confirm the plugin can see
  your cluster before relying on it for a real task.
- If your local server requires a different API key or none at all, set
  `LOCAL_API_KEY` accordingly — most local OpenAI-compatible servers accept
  any non-empty string.

## Troubleshooting

**LM Studio keeps "thinking" even with `LOCAL_SUPPRESS_THINKING=true`**
This is a known LM Studio limitation, not a bug in this proxy: as of this
writing, LM Studio's REST/OpenAI-compat API ignores `reasoning_effort` and
`thinking_enable` entirely — reasoning models always think regardless of
what a request sends (see
[lmstudio-bug-tracker#988](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/988)
and
[#2057](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/2057)).
The proxy deliberately sends no thinking-suppression fields for LM Studio
rather than send ones that silently do nothing. The only current workaround
is disabling reasoning from **Inference > Custom Fields** in the LM Studio
app itself. Check that tracker for whether it's since been fixed.

**`"No instance found for model <id>"` (HTTP 404 from the local cluster)**
The model is in Exo's catalog but has no running instance yet. Load one:

```bash
curl -s -X POST http://localhost:52415/place_instance \
  -H "Content-Type: application/json" \
  -d '{"model_id": "mlx-community/<model-id>"}'
```

Then poll `curl -s http://localhost:52415/state` until the runner shows
`RunnerIdle` before sending completion requests.

**`ModuleNotFoundError: No module named 'mlx'` in Exo's runner logs**
Exo's own virtualenv was set up without its `mlx` extra. From Exo's project
directory:

```bash
uv sync --extra mlx
```

then restart Exo. Watch for a build failure during this step (next item).

**`error: cannot execute tool 'metal' due to missing Metal Toolchain`**
`mlx` on Apple Silicon builds Metal GPU kernels from source, which needs the
Metal Toolchain component — a separate download from Xcode itself in recent
Xcode versions. Fix:

```bash
xcodebuild -downloadComponent MetalToolchain
```

Then retry `uv sync --extra mlx`. This is a ~700MB one-time download.

**Model picks the right file structure but wastes time "thinking" first**
Models with a reasoning/"thinking" mode (tagged `thinking` /
`thinking_toggle` in Exo's model catalog) can spend hundreds of tokens on
`reasoning_content` before ever emitting the actual output — pure overhead
for a fully-specified boilerplate request. In testing, setting
`enable_thinking: false` alone was inconsistent; pairing it with
`reasoning_effort: "none"` and a low `temperature` reliably suppressed it
(sub-second responses instead of tens of seconds). `router_proxy.py` sets
all three by default (`enable_thinking=false`, `reasoning_effort="none"`,
`temperature=0.2`) on every request it routes to the local cluster —
override them explicitly in the request body if you need thinking mode for
a specific offloaded call.

**Sizing a model to available memory**
MLX model weights load into unified memory, so a model's
`storage_size_megabytes` (from `GET /v1/models`) is roughly what it costs in
RAM, plus headroom for KV cache. Check real free memory before placing a
large instance — `top -l 1 | grep PhysMem` on macOS — rather than assuming
free RAM equals total RAM minus what a naive per-process memory sum
suggests; browsers and other apps can consume most of it. Prefer instances
that leave a few GB of headroom over the largest model that nominally fits.

**`stream: true` requests**
The proxy is built for single-shot mechanical code generation, not chat, and
does not support streaming — an earlier version crashed with a raw 500 when
a client set `stream: true` and it tried to parse an SSE response as JSON.
It now rejects streaming requests explicitly with a `400
streaming_not_supported` error before forwarding anything. Set `stream:
false` or omit the field entirely.

## Error handling

If the local cluster is offline or unreachable, `router_proxy.py` returns:

```json
{
  "error": {
    "message": "Local cluster at http://localhost:52415/v1 is unreachable. Fall back to the cloud model for this request.",
    "type": "local_cluster_offline"
  },
  "router_decision": "local",
  "router_reason": "explicit task_type=boilerplate"
}
```

with HTTP status `502`. A request that times out instead of failing to
connect returns HTTP `504` with `"type": "local_cluster_timeout"`. Per the
`local-offload` skill, the primary agent treats both as a signal to fall
back to doing the work itself on the cloud model rather than blocking or
retrying indefinitely.

## Security notes

- The local cluster is assumed to be trusted infrastructure on your own
  network (your own machine or LAN). Do not point `LOCAL_ENDPOINT` at an
  untrusted or public host.
- `sk-local` is a placeholder credential expected by most local
  OpenAI-compatible servers, not a real secret — it is not treated as
  sensitive by this plugin.
- The proxy does not log request/response bodies, only routing decisions
  and status codes.

---

# Skill: daisy-chain

No separate service to run — described in full in
[`plugins/claude-efficiency-suite/skills/daisy-chain/SKILL.md`](plugins/claude-efficiency-suite/skills/daisy-chain/SKILL.md) (also in `plugins/local-llm-router/`).

**What it does**: Exo already clusters devices on the same LAN with zero
configuration — the actual friction in "connect all your devices" is the
per-device install, not the clustering protocol. This skill front-loads
every diagnostic this plugin's own setup hit the hard way (missing `mlx`,
missing Metal Toolchain, Python-version compatibility, stale processes on
the port) into one pass, presents the *entire* install plan as a single
confirmation, and only then executes end-to-end — then verifies the device
actually joined the existing cluster (not just that Exo is running).

**How friction is actually reduced**: by batching consent into one
accurate, complete confirmation instead of several partial ones — not by
skipping consent. Installing software and downloading files still require
an explicit yes every time.

**What it deliberately doesn't do**: reach out to or configure other
devices remotely from one session. Run it *on* the device being added
(open a Claude Code session there, or an SSH session you're already
logged into) — a version that configures a fleet unattended from one
place has a meaningfully worse failure mode (one bad step affects every
device at once, with nobody watching any of them in real time), and was a
deliberate scope decision, not an oversight.

---

# Skill: grouping

No separate service to run — this skill is pure agent behavior, described
in full in [`plugins/claude-efficiency-suite/skills/grouping/SKILL.md`](plugins/claude-efficiency-suite/skills/grouping/SKILL.md) (also in `plugins/work-ops/`).

**What it does**: given a backlog (this session's tracked tasks, or a
documented to-do list you point it at), it groups related items so shared
context is loaded once per group rather than once per item, ranks the
groups by value, and assigns each one **both** a tier and an effort level:
the cheapest tier that can actually do it well (checking local-offload's
local model first, then Haiku, then Sonnet, then Opus only for groups that
genuinely need it), and independently, how much depth that group's work
gets (quick / standard / thorough) — a trivial Sonnet-tier rename and a
gnarly Sonnet-tier refactor don't need the same amount of verification. It
then works through the groups in priority order without stopping to check
in after each one, and reports one summary at the end.

**What it deliberately doesn't do**: turn itself into an unattended
background loop. It executes a bounded batch when you invoke it in a
session — it doesn't schedule itself or set an open-ended goal to keep
grinding through a backlog unsupervised. Recurring/scheduled runs are a
separate, explicit choice you make each time with the normal scheduling
tools, not something this skill starts on its own.

**Usage-limit-aware mode**: ask it things like "how much of this can I get
done" or "show me my options" and, instead of executing immediately, it
lays out three scenarios — best output (highest tier everywhere the budget
allows), fastest output (lightest sufficient tiers, flags what can run in
parallel), and most groups completed (cheapest-sufficient tier throughout)
— so you can pick the trade-off before committing.

**Usage**: just describe the backlog and ask, e.g. "group my current task
list and work through it efficiently" or "here's TODO.md, group and
prioritize it, use cheap models where you can." It composes with
`local-offload` automatically when a local endpoint is configured, and
works fine without it (ranking purely among Haiku/Sonnet/Opus).

---

# Skill: handoff

No separate service to run — described in full in
[`plugins/claude-efficiency-suite/skills/handoff/SKILL.md`](plugins/claude-efficiency-suite/skills/handoff/SKILL.md) (also in `plugins/work-ops/`).

**What it does**: writes and reads a structured, human-readable checkpoint
at `.claude/handoffs/<slug>.md` in your project — the goal, a per-group
status ledger, key decisions made along the way, and the next concrete
step. Two things can erase task-tracking state mid-work: the harness's own
context compaction (opaque, can blur specific structured state) and simply
starting a new conversation. This is a deliberate, durable checkpoint that
survives both, because it's a file in the project, not just conversation
memory.

**Usage**: ask directly — "checkpoint this", "let's continue later",
"where did we leave off", "I'm starting a new conversation, summarize
progress" — or let it happen automatically: `grouping` invokes it on its
own for batches of more than a handful of groups, updating the same
checkpoint file after each group completes rather than waiting until the
end. On resume, it reads the file, spot-checks a couple of "done" items
against actual current state before trusting the whole ledger (state goes
stale), and continues from the recorded next step rather than restarting
already-finished work.

**What it deliberately doesn't do**: replace the harness's automatic
compaction, or keep completed handoffs around indefinitely — once a
checkpoint's status is `done`, it should be deleted, not left to accumulate
and confuse future "which handoff is current" lookups.

## License

MIT
