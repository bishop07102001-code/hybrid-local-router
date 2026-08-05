# claude-efficiency-suite

The full suite — all five skills (`roadmap`, `grouping`, `local-offload`,
`daisy-chain`, `handoff`) in one plugin. Full documentation lives in the
[main repo README](../../README.md); this file is just a pointer.

Prefer a narrower install? [`local-llm-router`](../local-llm-router) has
just the local-LLM-routing skills; [`work-ops`](../work-ops) has just the
work-orchestration skills, no local hardware required.

## Setup

```bash
./install.sh
# then start your local LLM server (Exo/Ollama/LM Studio/vLLM/llama.cpp)
python3 scripts/router_proxy.py
curl -s http://localhost:8787/health | python3 -m json.tool
```

See the [main repo README](../../README.md) for the full breakdown of
each skill, configuration reference, and troubleshooting.
