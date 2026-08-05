# local-llm-router

The local-LLM-routing half of
[claude-efficiency-suite](https://github.com/bishop07102001-code/claude-efficiency-suite) —
`local-offload` and `daisy-chain` only, no work-orchestration skills. See
the [main repo README](../../README.md) for full documentation; this file
is just the quick-start for this plugin specifically.

## Skills

- **`local-offload`** — routes boilerplate/refactor sub-tasks to a local
  LLM (Exo, Ollama, LM Studio, vLLM, or llama.cpp — auto-detected) instead
  of your cloud model. Tracks usage (`GET /stats`) so savings are a real
  number. If no local backend exists yet, assesses your hardware and helps
  you set one up, with confirmation before installing anything.
- **`daisy-chain`** — adds a device to an existing Exo cluster with one
  consolidated confirmation instead of per-step prompts.

## Setup

```bash
./install.sh
# then start your local LLM server (Exo/Ollama/LM Studio/vLLM/llama.cpp)
python3 scripts/router_proxy.py
curl -s http://localhost:8787/health | python3 -m json.tool
```

Full configuration reference, troubleshooting, and the "Automatic model
discovery & provisioning" details are in the
[main repo README](../../README.md#skill-local-offload).

Want `roadmap`/`grouping`/`handoff` too? Install
[`work-ops`](../work-ops) alongside this, or install
[`claude-efficiency-suite`](../claude-efficiency-suite) instead for all five in
one plugin.
