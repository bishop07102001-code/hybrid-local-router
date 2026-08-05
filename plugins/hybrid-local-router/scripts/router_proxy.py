#!/usr/bin/env python3
"""
router_proxy.py

Lightweight OpenAI-compatible proxy that routes repetitive code-generation /
boilerplate / refactor requests to a local LLM cluster while passing
everything else through untouched so the caller can send it to a cloud
model instead.

The proxy auto-detects which local backend it's talking to — Exo, Ollama,
LM Studio, vLLM, or llama.cpp — and manages the model lifecycle itself for
each: if a suitable model is already running it's used as-is (with request
parameters adjusted for offload work — different field names per backend);
if nothing is running (Exo/Ollama/LM Studio only — vLLM and llama.cpp are
commonly deployed with one model fixed at launch, already loaded before the
server accepts connections), it checks available RAM (and, for Exo, disk)
and picks the best-fitting model (preferring code-capable ones), provisions
it, and warms it up in the background before routing requests to it.
Ollama/LM Studio selection only considers already-downloaded models —
auto-downloading a new one would be an unprompted multi-gigabyte action, a
separate explicitly-confirmed step this plugin doesn't take silently.
Auto-discovery/provisioning is skipped entirely for any other backend
(text-generation-webui, ...) — the caller's requested model field is
forwarded as-is. If no local model can be used at all, the request falls
back to an optional cloud upstream, or is returned
to the caller as pass-through.

Usage:
    python3 router_proxy.py
    # or
    uvicorn router_proxy:app --host 0.0.0.0 --port 8787

Environment variables:
    LOCAL_ENDPOINT          Base URL of the local OpenAI-compatible server.
                             Default: http://localhost:52415/v1
    LOCAL_API_KEY            API key sent to the local server.
                             Default: sk-local
    LOCAL_CONTROL_URL        Base URL of the local backend's control/
                             management API — Exo's instance-placement API,
                             or Ollama's native API (/api/tags, /api/ps) —
                             not the OpenAI-compatible /v1 surface. Default:
                             derived from LOCAL_ENDPOINT by stripping a
                             trailing /v1. The backend is auto-detected by
                             probing this URL; if neither Exo nor Ollama
                             responds there, auto-discovery/provisioning is
                             skipped and requests are forwarded with the
                             caller's model field as-is.
    AUTO_PROVISION_MODEL     Whether to auto-load a model when none is
                             running (Exo: places+waits+warms an instance;
                             Ollama: warms up the best already-pulled
                             model). Default: true
    MODEL_RAM_HEADROOM_MB    RAM to leave free (beyond a candidate model's
                             size) when picking a model to auto-provision.
                             Default: 2048
    MODEL_DISK_HEADROOM_MB   Disk space to leave free when picking a model
                             to auto-provision (Exo only — Ollama selection
                             is restricted to already-downloaded models, so
                             there's no new disk usage to budget for).
                             Default: 5120
    MODEL_CACHE_PATH         Filesystem path used to check free disk space
                             (the volume your local server downloads models
                             onto). Default: ~
    MODEL_PROVISION_TIMEOUT_SECONDS
                             Max time to wait for an auto-provisioned Exo
                             instance to finish downloading/loading (before
                             warmup). Default: 600
    MODEL_PROVISION_POLL_INTERVAL_SECONDS
                             How often to poll cluster state while waiting.
                             Default: 5
    MODEL_WARMUP_TIMEOUT_SECONDS
                             Max time the background task waits for the
                             post-load warmup call to finish. Default: 600
    CLOUD_UPSTREAM_URL       Optional base URL of a cloud OpenAI-compatible
                             endpoint to forward non-offload requests to, and
                             the fallback tier when no local model can be
                             used at all. If unset, such requests are
                             returned to the caller with pass_through=true
                             instead, so the caller (the primary agent) can
                             handle them on its own cloud model.
    CLOUD_UPSTREAM_API_KEY   API key sent to CLOUD_UPSTREAM_URL, if configured.
    ROUTER_HOST              Host to bind the proxy server to. Default: 0.0.0.0
    ROUTER_PORT              Port to bind the proxy server to. Default: 8787
    ROUTER_TIMEOUT_SECONDS   Per-completion-request timeout in seconds. Default: 120
    ROUTER_HEALTH_TIMEOUT_SECONDS
                             Timeout for local-cluster/control-plane checks. Default: 3
    LOCAL_SUPPRESS_THINKING  Send a backend-appropriate "disable reasoning"
                             field (enable_thinking/reasoning_effort for
                             Exo, think for Ollama) and a low temperature by
                             default on local calls. Default: true
    LOCAL_ALLOW_AUTO_DOWNLOAD
                             Whether Exo may pick and place a brand-new
                             model from its catalog when nothing is
                             running (this can trigger a multi-gigabyte
                             download). Default: false — matches Ollama/LM
                             Studio, which never trigger new downloads on
                             their own. When false, Exo will still use or
                             warm up an already-loaded model, just won't
                             provision a new one from scratch.
    USAGE_DB_PATH            SQLite file the proxy logs routed requests to,
                             for the GET /stats endpoint. Default:
                             ~/.hybrid-local-router/usage.sqlite3
    CLOUD_INPUT_COST_PER_MTOK / CLOUD_OUTPUT_COST_PER_MTOK
                             USD per million input/output tokens, used only
                             to *estimate* cloud cost avoided by local
                             routing in GET /stats. These are placeholders
                             (Sonnet-ish order of magnitude) — set them to
                             your actual plan's effective per-token cost
                             for a meaningful number. Default: 3.0 / 15.0
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sqlite3
import time
from typing import Any, Literal

import httpx
import psutil
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

logging.basicConfig(
    level=os.environ.get("ROUTER_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s router_proxy: %(message)s",
)
logger = logging.getLogger("router_proxy")

LOCAL_ENDPOINT = os.environ.get("LOCAL_ENDPOINT", "http://localhost:52415/v1").rstrip("/")
LOCAL_API_KEY = os.environ.get("LOCAL_API_KEY", "sk-local")


def _derive_exo_control_url(local_endpoint: str) -> str:
    if local_endpoint.endswith("/v1"):
        return local_endpoint[: -len("/v1")]
    return local_endpoint


LOCAL_CONTROL_URL = os.environ.get("LOCAL_CONTROL_URL", "").rstrip("/") or _derive_exo_control_url(
    LOCAL_ENDPOINT
)

AUTO_PROVISION_MODEL = os.environ.get("AUTO_PROVISION_MODEL", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)
MODEL_RAM_HEADROOM_MB = float(os.environ.get("MODEL_RAM_HEADROOM_MB", "2048"))
MODEL_DISK_HEADROOM_MB = float(os.environ.get("MODEL_DISK_HEADROOM_MB", "5120"))
MODEL_CACHE_PATH = os.environ.get("MODEL_CACHE_PATH", "~")
MODEL_PROVISION_TIMEOUT_SECONDS = float(os.environ.get("MODEL_PROVISION_TIMEOUT_SECONDS", "600"))
MODEL_PROVISION_POLL_INTERVAL_SECONDS = float(
    os.environ.get("MODEL_PROVISION_POLL_INTERVAL_SECONDS", "5")
)
MODEL_WARMUP_TIMEOUT_SECONDS = float(os.environ.get("MODEL_WARMUP_TIMEOUT_SECONDS", "600"))
READY_RUNNER_STATES = {"RunnerIdle", "RunnerReady"}

CLOUD_UPSTREAM_URL = os.environ.get("CLOUD_UPSTREAM_URL", "").rstrip("/") or None
CLOUD_UPSTREAM_API_KEY = os.environ.get("CLOUD_UPSTREAM_API_KEY", "")

ROUTER_HOST = os.environ.get("ROUTER_HOST", "0.0.0.0")
ROUTER_PORT = int(os.environ.get("ROUTER_PORT", "8787"))
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("ROUTER_TIMEOUT_SECONDS", "120"))
HEALTH_TIMEOUT_SECONDS = float(os.environ.get("ROUTER_HEALTH_TIMEOUT_SECONDS", "3"))

# enable_thinking / reasoning_effort are Exo + mlx-lm specific chat-completion
# extensions, not part of the OpenAI spec. They measurably speed up offloaded
# boilerplate calls on thinking-capable models (see README troubleshooting),
# but a strict OpenAI-compatible server (some vLLM/LM Studio configs) may
# reject unrecognized fields outright. Defaults on since Exo is this plugin's
# primary target; set LOCAL_SUPPRESS_THINKING=false if your local server is
# not Exo/mlx-lm and rejects unrecognized request fields.
LOCAL_SUPPRESS_THINKING = os.environ.get("LOCAL_SUPPRESS_THINKING", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)

LOCAL_ALLOW_AUTO_DOWNLOAD = os.environ.get("LOCAL_ALLOW_AUTO_DOWNLOAD", "false").strip().lower() in (
    "1",
    "true",
    "yes",
)

USAGE_DB_PATH = os.environ.get(
    "USAGE_DB_PATH", os.path.expanduser("~/.hybrid-local-router/usage.sqlite3")
)
CLOUD_INPUT_COST_PER_MTOK = float(os.environ.get("CLOUD_INPUT_COST_PER_MTOK", "3.0"))
CLOUD_OUTPUT_COST_PER_MTOK = float(os.environ.get("CLOUD_OUTPUT_COST_PER_MTOK", "15.0"))

# Serializes auto-provisioning so two concurrent offload requests that both
# find no running instance don't both trigger a placement.
_provision_lock = asyncio.Lock()

# Explicit task types the caller can set to force a routing decision without
# relying on the heuristic classifier below.
OFFLOAD_TASK_TYPES = {"boilerplate", "refactor", "codegen", "scaffold", "syntax"}
CLOUD_TASK_TYPES = {"architecture", "planning", "review", "design", "reasoning"}

# Heuristic keyword patterns used when the caller does not set `task_type`.
# Matched case-insensitively against the concatenated message content.
OFFLOAD_PATTERNS = [
    r"\bboilerplate\b",
    r"\bscaffold(ing)?\b",
    r"\bgenerate (a |the )?(file|class|component|module|test|dto|model|schema|endpoint|crud)\b",
    r"\brefactor\b",
    r"\brename\b",
    r"\bconvert (this|the following|to)\b",
    r"\breformat\b",
    r"\bcrud\b",
    r"\brepetitive\b",
    r"\bfor each (file|item|entity|field)\b",
    r"\bfollowing the (same )?pattern\b",
    r"\bcallback(s)? to async\b",
    r"\bupdate import(s)?\b",
]
OFFLOAD_REGEX = re.compile("|".join(OFFLOAD_PATTERNS), re.IGNORECASE)

# Signals that a request needs judgment and should stay on the cloud model
# even if it superficially matches an offload keyword.
CLOUD_PATTERNS = [
    r"\barchitecture\b",
    r"\btrade-?off(s)?\b",
    r"\bdesign decision\b",
    r"\bsecurity\b",
    r"\bauth(entication|orization)?\b",
    r"\bwhich (approach|framework|library) should\b",
    r"\bis it (a good idea|safe|correct) to\b",
]
CLOUD_REGEX = re.compile("|".join(CLOUD_PATTERNS), re.IGNORECASE)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool | None = False
    task_type: str | None = Field(
        default=None,
        description=(
            "Optional explicit routing hint. One of: "
            f"{sorted(OFFLOAD_TASK_TYPES | CLOUD_TASK_TYPES)}. "
            "If omitted, the request body's messages are classified heuristically."
        ),
    )


RoutingDecision = Literal["local", "cloud_upstream", "pass_through"]


def classify_task(payload: ChatCompletionRequest) -> tuple[RoutingDecision, str]:
    """Decide whether a chat completion request should be offloaded to the
    local cluster. Returns (decision, reason)."""
    if payload.task_type:
        normalized = payload.task_type.strip().lower()
        if normalized in OFFLOAD_TASK_TYPES:
            return "local", f"explicit task_type={normalized}"
        if normalized in CLOUD_TASK_TYPES:
            decision: RoutingDecision = "cloud_upstream" if CLOUD_UPSTREAM_URL else "pass_through"
            return decision, f"explicit task_type={normalized}"
        # Unrecognized task_type falls through to heuristic classification.

    combined_content = "\n".join(m.content for m in payload.messages if m.content)

    if CLOUD_REGEX.search(combined_content):
        decision = "cloud_upstream" if CLOUD_UPSTREAM_URL else "pass_through"
        return decision, "heuristic match: requires judgment/design"

    if OFFLOAD_REGEX.search(combined_content):
        return "local", "heuristic match: boilerplate/refactor pattern"

    decision = "cloud_upstream" if CLOUD_UPSTREAM_URL else "pass_through"
    return decision, "no offload pattern matched"


# --- Local capacity / model lifecycle -------------------------------------


def get_free_ram_mb() -> float:
    """Memory actually available for new allocations, not just "free" pages
    — matches what the OS would reclaim under pressure (reclaimable cache,
    purgeable memory), which is what a model load actually competes for."""
    return psutil.virtual_memory().available / (1024 * 1024)


def get_free_disk_mb(path: str) -> float:
    expanded = os.path.expanduser(path)
    if not os.path.exists(expanded):
        expanded = os.path.expanduser("~")
    return shutil.disk_usage(expanded).free / (1024 * 1024)


async def _get_exo_state(client: httpx.AsyncClient) -> dict[str, Any] | None:
    """Fetch Exo's cluster state. Returns None if the control-plane API
    isn't reachable or isn't Exo (e.g. Ollama/vLLM/LM Studio) — callers
    should treat that as "auto-management not available here"."""
    try:
        resp = await client.get(f"{LOCAL_CONTROL_URL}/state", timeout=HEALTH_TIMEOUT_SECONDS)
        if resp.status_code != 200:
            return None
        return resp.json()
    except (httpx.HTTPError, ValueError):
        return None


async def _get_model_catalog(client: httpx.AsyncClient) -> list[dict[str, Any]] | None:
    try:
        resp = await client.get(
            f"{LOCAL_ENDPOINT}/models",
            headers={"Authorization": f"Bearer {LOCAL_API_KEY}"},
            timeout=HEALTH_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])
    except (httpx.HTTPError, ValueError):
        return None


# Which local backend LOCAL_ENDPOINT points at — detected once (by probing
# each backend's distinctive control API) and cached for the process
# lifetime. "exo" and "ollama" get auto-discovery/provisioning; anything
# else ("generic") just forwards the caller's model field as-is.
_detected_backend: str | None = None


async def _detect_backend(client: httpx.AsyncClient) -> str:
    global _detected_backend
    if _detected_backend is not None:
        return _detected_backend
    try:
        resp = await client.get(f"{LOCAL_CONTROL_URL}/state", timeout=HEALTH_TIMEOUT_SECONDS)
        if resp.status_code == 200 and "instances" in resp.json():
            _detected_backend = "exo"
            return _detected_backend
    except (httpx.HTTPError, ValueError):
        pass
    try:
        resp = await client.get(f"{LOCAL_CONTROL_URL}/api/tags", timeout=HEALTH_TIMEOUT_SECONDS)
        if resp.status_code == 200 and "models" in resp.json():
            _detected_backend = "ollama"
            return _detected_backend
    except (httpx.HTTPError, ValueError):
        pass
    try:
        resp = await client.get(f"{LOCAL_CONTROL_URL}/api/v1/models", timeout=HEALTH_TIMEOUT_SECONDS)
        if resp.status_code == 200 and "models" in resp.json():
            _detected_backend = "lmstudio"
            return _detected_backend
    except (httpx.HTTPError, ValueError):
        pass
    try:
        resp = await client.get(f"{LOCAL_CONTROL_URL}/version", timeout=HEALTH_TIMEOUT_SECONDS)
        if resp.status_code == 200:
            _detected_backend = "vllm"
            return _detected_backend
    except httpx.HTTPError:
        pass
    try:
        resp = await client.get(f"{LOCAL_CONTROL_URL}/props", timeout=HEALTH_TIMEOUT_SECONDS)
        if resp.status_code == 200:
            _detected_backend = "llamacpp"
            return _detected_backend
    except httpx.HTTPError:
        pass
    _detected_backend = "generic"
    return _detected_backend


def _thinking_suppression_fields(backend: str) -> dict[str, Any]:
    """enable_thinking/reasoning_effort (Exo/mlx-lm) and think (Ollama) are
    different field names for the same idea on different backends — neither
    is standard OpenAI. Suppressing reasoning matters here because offload
    requests are mechanical: the caller already did the design thinking.
    LM Studio deliberately gets nothing here: as of this writing its REST/
    OpenAI-compat API ignores reasoning_effort and thinking_enable entirely
    (open bugs in lmstudio-ai/lmstudio-bug-tracker #988, #2057) — reasoning
    can currently only be toggled from Inference > Custom Fields in the app
    itself, not per-request. Sending a field that's silently ignored would
    be misleading, so we don't."""
    if not LOCAL_SUPPRESS_THINKING:
        return {}
    if backend == "exo":
        return {"enable_thinking": False, "reasoning_effort": "none", "temperature": 0.2}
    if backend == "ollama":
        return {"think": False, "temperature": 0.2}
    if backend in ("vllm", "llamacpp"):
        # Documented mechanism for both (Jinja chat-template kwargs
        # passthrough): chat_template_kwargs.enable_thinking plus
        # reasoning_effort as a redundant signal, mirroring the combo that
        # proved necessary for reliability on Exo. Only takes effect for
        # models whose chat template actually reads enable_thinking (e.g.
        # Qwen3-style); harmless no-op otherwise.
        return {
            "chat_template_kwargs": {"enable_thinking": False},
            "reasoning_effort": "none",
            "temperature": 0.2,
        }
    return {}


# Model ids that have completed at least one successful completion request
# (either our own warmup call or a real request). Exo's runner state (and
# Ollama's /api/ps) can report a model as "loaded" as soon as weights are in
# memory — well before the first inference's kernel-compilation/cold-load
# cost is paid — so "loaded" alone is not a safe signal to route a real,
# latency-sensitive request to. Only "warm" is.
_warmed_model_ids: set[str] = set()


async def _warmup_model(client: httpx.AsyncClient, model_id: str) -> None:
    """A model's first inference call can pay a one-time cold-start cost
    (observed 2+ minutes on some hardware/backends even for a tiny
    completion). Pay that cost here, inside the provisioning window the
    caller is already prepared to wait through, instead of on their actual
    request (best-effort: failure here doesn't block using the model, it
    just means the caller's own request pays the warmup cost)."""
    try:
        backend = await _detect_backend(client)
        extra = _thinking_suppression_fields(backend)
        resp = await client.post(
            f"{LOCAL_ENDPOINT}/chat/completions",
            json={
                "model": model_id,
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 1,
                **extra,
            },
            headers={"Authorization": f"Bearer {LOCAL_API_KEY}", "Content-Type": "application/json"},
            timeout=MODEL_WARMUP_TIMEOUT_SECONDS,
        )
        if resp.status_code < 400:
            _warmed_model_ids.add(model_id)
    except httpx.HTTPError as exc:
        logger.warning("warmup call for %s failed (continuing anyway): %s", model_id, exc)


async def _place_instance(client: httpx.AsyncClient, model_id: str) -> bool:
    try:
        resp = await client.post(
            f"{LOCAL_CONTROL_URL}/place_instance",
            json={"model_id": model_id},
            timeout=HEALTH_TIMEOUT_SECONDS,
        )
        return resp.status_code < 400
    except httpx.HTTPError:
        return False


def _instance_model_ids(state: dict[str, Any]) -> list[tuple[str, str]]:
    """(instance_id, model_id) for every instance in cluster state, across
    Exo's instance variants (MlxRingInstance, MlxJacclInstance, ...)."""
    out: list[tuple[str, str]] = []
    for inst_id, inst in state.get("instances", {}).items():
        for variant in inst.values():
            model_id = variant.get("shardAssignments", {}).get("modelId")
            if model_id:
                out.append((inst_id, model_id))
    return out


def _instance_runner_states(state: dict[str, Any], instance_id: str) -> list[str]:
    inst = state.get("instances", {}).get(instance_id, {})
    runner_ids: list[str] = []
    for variant in inst.values():
        runner_to_shard = variant.get("shardAssignments", {}).get("runnerToShard", {})
        runner_ids.extend(runner_to_shard.keys())
    runners = state.get("runners", {})
    states = []
    for rid in runner_ids:
        runner = runners.get(rid)
        if runner:
            states.append(next(iter(runner.keys()), "Unknown"))
    return states


def _find_ready_local_model(state: dict[str, Any]) -> str | None:
    """A model whose weights are loaded (Exo's runner state) — NOT
    necessarily fast yet. See _find_warm_local_model for the safe-to-route
    check; this one is only used to detect "something is loaded that we
    haven't proven warm" so it can be warmed up rather than provisioning a
    redundant second model."""
    for inst_id, model_id in _instance_model_ids(state):
        runner_states = _instance_runner_states(state, inst_id)
        if runner_states and all(s in READY_RUNNER_STATES for s in runner_states):
            return model_id
    return None


def _find_warm_local_model(state: dict[str, Any]) -> str | None:
    """A model that is both loaded AND has completed at least one
    successful inference (see _warmed_model_ids) — safe to route a real,
    latency-sensitive request to without risking the first-inference
    compile cost."""
    for inst_id, model_id in _instance_model_ids(state):
        if model_id not in _warmed_model_ids:
            continue
        runner_states = _instance_runner_states(state, inst_id)
        if runner_states and all(s in READY_RUNNER_STATES for s in runner_states):
            return model_id
    return None


def pick_best_model(
    catalog: list[dict[str, Any]], ram_budget_mb: float, disk_budget_mb: float
) -> dict[str, Any] | None:
    """Pick the best model that fits the given budget: code-capable models
    first, then the largest model (by weight size) within budget — bigger
    models generally perform better, and code-tagged models are a better
    fit for this plugin's boilerplate/refactor workload than general-purpose
    ones of similar size."""
    budget_mb = min(ram_budget_mb, disk_budget_mb)
    fits = [m for m in catalog if m.get("storage_size_megabytes", float("inf")) <= budget_mb]
    if not fits:
        return None
    code_capable = [m for m in fits if "code" in (m.get("capabilities") or [])]
    pool = code_capable if code_capable else fits
    pool.sort(key=lambda m: -m.get("storage_size_megabytes", 0))
    return pool[0]


# Cold-start compilation time for a freshly-loaded model scales with model
# size/architecture and was observed to exceed 5 minutes for a 32GB model —
# there's no timeout short enough to both (a) reliably cover the worst case
# and (b) not make a caller's HTTP request hang unacceptably long. So
# provisioning + warmup run as a detached background task instead: the
# request that triggers provisioning always falls back immediately (to
# cloud_upstream/pass_through), and only *subsequent* requests, once the
# background task finishes, get to use the newly-ready local model.
_provisioning_task: asyncio.Task[None] | None = None
_provisioning_model_id: str | None = None
_provisioning_started_at: float | None = None


async def _resolve_local_model(client: httpx.AsyncClient, requested_model: str) -> tuple[str | None, str]:
    """Decide which model_id to actually send the local completion request
    for. Returns (model_id, note); model_id is None when there's no local
    model ready right now — either because none could be provisioned, or
    because provisioning just started in the background (caller should fall
    back to cloud_upstream/pass_through for *this* request)."""
    backend = await _detect_backend(client)
    if backend == "exo":
        return await _resolve_exo_model(client, requested_model)
    if backend == "ollama":
        return await _resolve_ollama_model(client, requested_model)
    if backend == "lmstudio":
        return await _resolve_lmstudio_model(client, requested_model)
    if backend == "vllm":
        return await _resolve_fixed_single_model(client, "vLLM")
    if backend == "llamacpp":
        return await _resolve_fixed_single_model(client, "llama.cpp")
    # No recognized control-plane API — trust whatever the caller asked for
    # and let the completion call itself succeed or fail (e.g.
    # text-generation-webui manages its own loaded model outside this
    # proxy's control).
    return requested_model, "no recognized local control plane detected; using requested model as-is"


async def _resolve_exo_model(client: httpx.AsyncClient, requested_model: str) -> tuple[str | None, str]:
    global _provisioning_task, _provisioning_model_id, _provisioning_started_at

    state = await _get_exo_state(client)
    if state is None:
        return requested_model, "Exo control plane unreachable; using requested model as-is"

    warm_model = _find_warm_local_model(state)
    if warm_model:
        return warm_model, f"using already-running local model {warm_model}"

    if _provisioning_task is not None and not _provisioning_task.done():
        elapsed = time.monotonic() - (_provisioning_started_at or time.monotonic())
        return None, (
            f"local model {_provisioning_model_id} is still loading/warming "
            f"in the background ({elapsed:.0f}s so far) — falling back for "
            f"this request; it should be ready for subsequent requests"
        )

    if not AUTO_PROVISION_MODEL:
        return None, "no running local instance and AUTO_PROVISION_MODEL is disabled"

    async with _provision_lock:
        # Re-check under the lock: another request may have started
        # provisioning (or one may have just finished) while we waited.
        state = await _get_exo_state(client)
        warm_model = _find_warm_local_model(state) if state else None
        if warm_model:
            return warm_model, f"using already-running local model {warm_model}"
        if _provisioning_task is not None and not _provisioning_task.done():
            elapsed = time.monotonic() - (_provisioning_started_at or time.monotonic())
            return None, (
                f"local model {_provisioning_model_id} is still loading/warming "
                f"in the background ({elapsed:.0f}s so far) — falling back for "
                f"this request"
            )

        # Something may already be loaded (e.g. from a previous proxy
        # process lifetime, or placed manually outside this proxy) but not
        # yet proven warm by us. Warm that up rather than provisioning a
        # redundant second model on top of it.
        ready_unwarmed_model = _find_ready_local_model(state) if state else None
        if ready_unwarmed_model:
            logger.info(
                "found loaded-but-unwarmed local model %s; warming it up in the background",
                ready_unwarmed_model,
            )
            _provisioning_model_id = ready_unwarmed_model
            _provisioning_started_at = time.monotonic()
            _provisioning_task = asyncio.create_task(_warmup_only_in_background(ready_unwarmed_model))
            return None, (
                f"local model {ready_unwarmed_model} is loaded but not yet "
                f"proven warm; warming it up in the background — falling "
                f"back for this request"
            )

        if not LOCAL_ALLOW_AUTO_DOWNLOAD:
            # Picking a new model from Exo's catalog and placing it can
            # trigger a multi-gigabyte download of a model that's never
            # been used before. That's a real download action, not just
            # a "load what's already on disk" step (unlike the branch
            # above, which only warms something already loaded) — so it
            # stays off by default, matching Ollama/LM Studio, which never
            # trigger new downloads on their own either.
            return None, (
                "no local model is loaded and LOCAL_ALLOW_AUTO_DOWNLOAD is "
                "false, so a new model won't be auto-downloaded — set "
                "LOCAL_ALLOW_AUTO_DOWNLOAD=true to allow it, or load a "
                "model yourself via Exo's dashboard/API"
            )

        catalog = await _get_model_catalog(client)
        if not catalog:
            return None, "could not read local model catalog"

        ram_mb = get_free_ram_mb()
        disk_mb = get_free_disk_mb(MODEL_CACHE_PATH)
        ram_budget = max(0.0, ram_mb - MODEL_RAM_HEADROOM_MB)
        disk_budget = max(0.0, disk_mb - MODEL_DISK_HEADROOM_MB)
        chosen = pick_best_model(catalog, ram_budget, disk_budget)
        if chosen is None:
            return None, (
                f"no local model fits available capacity (free RAM "
                f"{ram_mb:.0f}MB, free disk {disk_mb:.0f}MB, budget after "
                f"headroom {min(ram_budget, disk_budget):.0f}MB)"
            )

        model_id = chosen["id"]
        logger.info(
            "starting background auto-provisioning of %s (%.0fMB, code_capable=%s)",
            model_id,
            chosen.get("storage_size_megabytes", 0),
            "code" in (chosen.get("capabilities") or []),
        )
        if not await _place_instance(client, model_id):
            return None, f"failed to request instance placement for {model_id}"

        _provisioning_model_id = model_id
        _provisioning_started_at = time.monotonic()
        _provisioning_task = asyncio.create_task(_provision_and_warmup_in_background(model_id))

        return None, (
            f"no local model was ready; started auto-provisioning {model_id} "
            f"in the background (large models can take several minutes to "
            f"load and warm up the first time) — falling back for this request"
        )


async def _provision_and_warmup_in_background(model_id: str) -> None:
    global _provisioning_task, _provisioning_model_id, _provisioning_started_at
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            deadline = time.monotonic() + MODEL_PROVISION_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                await asyncio.sleep(MODEL_PROVISION_POLL_INTERVAL_SECONDS)
                state = await _get_exo_state(client)
                if not state:
                    continue
                for inst_id, mid in _instance_model_ids(state):
                    if mid != model_id:
                        continue
                    runner_states = _instance_runner_states(state, inst_id)
                    if any(s == "RunnerFailed" for s in runner_states):
                        logger.error("auto-provisioned model %s failed to load", model_id)
                        return
                    if runner_states and all(s in READY_RUNNER_STATES for s in runner_states):
                        logger.info("warming up %s before marking it ready", model_id)
                        await _warmup_model(client, model_id)
                        logger.info("%s is now provisioned and warm", model_id)
                        return
            logger.warning(
                "timed out waiting for %s to become ready after %.0fs",
                model_id,
                MODEL_PROVISION_TIMEOUT_SECONDS,
            )
    finally:
        _provisioning_task = None
        _provisioning_model_id = None
        _provisioning_started_at = None


async def _warmup_only_in_background(model_id: str) -> None:
    """Like _provision_and_warmup_in_background but for a model that's
    already loaded — skip straight to the warmup call."""
    global _provisioning_task, _provisioning_model_id, _provisioning_started_at
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            await _warmup_model(client, model_id)
            logger.info("%s is now warm", model_id)
    finally:
        _provisioning_task = None
        _provisioning_model_id = None
        _provisioning_started_at = None


# --- Ollama backend ----------------------------------------------------------
#
# Ollama has no separate "place an instance" step — a model loads on its
# first request — so there's nothing to poll for readiness; provisioning
# here means the same warmup call used for Exo. Selection is also
# deliberately narrower: only models the user has already pulled are
# considered. Auto-`ollama pull`ing a model neither requested nor already
# present would be an unprompted multi-gigabyte download, which is a
# separate, explicitly-confirmed action this plugin doesn't take silently.

CODE_NAME_PATTERN = re.compile(r"code|coder|starcoder|codellama|granite-code", re.IGNORECASE)


async def _get_ollama_running(client: httpx.AsyncClient) -> list[dict[str, Any]] | None:
    try:
        resp = await client.get(f"{LOCAL_CONTROL_URL}/api/ps", timeout=HEALTH_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json().get("models", [])
    except (httpx.HTTPError, ValueError):
        return None


async def _get_ollama_local_models(client: httpx.AsyncClient) -> list[dict[str, Any]] | None:
    try:
        resp = await client.get(f"{LOCAL_CONTROL_URL}/api/tags", timeout=HEALTH_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json().get("models", [])
    except (httpx.HTTPError, ValueError):
        return None


def _pick_best_ollama_model(
    models: list[dict[str, Any]], ram_budget_mb: float
) -> dict[str, Any] | None:
    """Best already-pulled model that fits the RAM budget: code-named
    models first, then the largest."""
    fits = [m for m in models if (m.get("size", float("inf")) / (1024 * 1024)) <= ram_budget_mb]
    if not fits:
        return None
    code_capable = [m for m in fits if CODE_NAME_PATTERN.search(m.get("name") or m.get("model") or "")]
    pool = code_capable if code_capable else fits
    pool.sort(key=lambda m: -m.get("size", 0))
    return pool[0]


def _warm_ollama_model_name(running: list[dict[str, Any]]) -> str | None:
    for m in running:
        name = m.get("name") or m.get("model")
        if name and name in _warmed_model_ids:
            return name
    return None


async def _resolve_ollama_model(client: httpx.AsyncClient, requested_model: str) -> tuple[str | None, str]:
    global _provisioning_task, _provisioning_model_id, _provisioning_started_at

    running = await _get_ollama_running(client) or []
    warm_model = _warm_ollama_model_name(running)
    if warm_model:
        return warm_model, f"using already-running local model {warm_model}"

    if _provisioning_task is not None and not _provisioning_task.done():
        elapsed = time.monotonic() - (_provisioning_started_at or time.monotonic())
        return None, (
            f"local model {_provisioning_model_id} is still loading/warming "
            f"in the background ({elapsed:.0f}s so far) — falling back for "
            f"this request"
        )

    if not AUTO_PROVISION_MODEL:
        return None, "no running local model and AUTO_PROVISION_MODEL is disabled"

    async with _provision_lock:
        running = await _get_ollama_running(client) or []
        warm_model = _warm_ollama_model_name(running)
        if warm_model:
            return warm_model, f"using already-running local model {warm_model}"
        if _provisioning_task is not None and not _provisioning_task.done():
            elapsed = time.monotonic() - (_provisioning_started_at or time.monotonic())
            return None, (
                f"local model {_provisioning_model_id} is still loading/warming "
                f"in the background ({elapsed:.0f}s so far) — falling back for "
                f"this request"
            )

        loaded_name = (running[0].get("name") or running[0].get("model")) if running else None
        if loaded_name:
            # Something's loaded (by us in a prior process lifetime, or by
            # the user directly) but not proven warm — warm it up instead
            # of loading a redundant second model.
            logger.info(
                "found loaded-but-unwarmed Ollama model %s; warming it up in the background",
                loaded_name,
            )
            _provisioning_model_id = loaded_name
            _provisioning_started_at = time.monotonic()
            _provisioning_task = asyncio.create_task(_warmup_only_in_background(loaded_name))
            return None, (
                f"local model {loaded_name} is loaded but not yet proven "
                f"warm; warming it up in the background — falling back for "
                f"this request"
            )

        models = await _get_ollama_local_models(client)
        if not models:
            return None, "no local Ollama models are pulled (run: ollama pull <model>)"

        ram_mb = get_free_ram_mb()
        ram_budget = max(0.0, ram_mb - MODEL_RAM_HEADROOM_MB)
        chosen = _pick_best_ollama_model(models, ram_budget)
        if chosen is None:
            return None, (
                f"no already-pulled Ollama model fits available RAM (free "
                f"{ram_mb:.0f}MB, budget after headroom {ram_budget:.0f}MB) "
                f"— pull a smaller model or free up memory"
            )

        model_id = chosen.get("name") or chosen.get("model")
        logger.info(
            "loading and warming up already-pulled Ollama model %s (%.0fMB, code_named=%s)",
            model_id,
            chosen.get("size", 0) / (1024 * 1024),
            bool(CODE_NAME_PATTERN.search(model_id or "")),
        )
        _provisioning_model_id = model_id
        _provisioning_started_at = time.monotonic()
        _provisioning_task = asyncio.create_task(_warmup_only_in_background(model_id))
        return None, (
            f"no Ollama model was loaded; loading and warming up {model_id} "
            f"in the background — falling back for this request"
        )


# --- LM Studio backend --------------------------------------------------------
#
# LM Studio's v1 REST API (/api/v1/models) lists every downloaded model —
# LLM and embedding alike — with a `loaded_instances` array (empty when not
# loaded) and `size_bytes`. Unlike Ollama, loading is an explicit call
# (POST /api/v1/models/load) rather than implicit-on-first-request; unlike
# Exo, it's not polled for readiness — the load call itself is synchronous,
# so it's wrapped in the same background task as everything else. Selection
# is restricted to already-downloaded models for the same reason as Ollama:
# this plugin doesn't trigger new downloads on its own.

LMSTUDIO_MODELS_PATH = "/api/v1/models"


async def _get_lmstudio_models(client: httpx.AsyncClient) -> list[dict[str, Any]] | None:
    try:
        resp = await client.get(f"{LOCAL_CONTROL_URL}{LMSTUDIO_MODELS_PATH}", timeout=HEALTH_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json().get("models", [])
    except (httpx.HTTPError, ValueError):
        return None


def _lmstudio_model_name(model: dict[str, Any]) -> str | None:
    return model.get("key") or model.get("id") or model.get("display_name")


def _pick_best_lmstudio_model(
    models: list[dict[str, Any]], ram_budget_mb: float
) -> dict[str, Any] | None:
    """Best already-downloaded LLM (not embedding) model that fits the RAM
    budget: code-named models first, then the largest."""
    candidates = [m for m in models if m.get("type") == "llm"]
    fits = [m for m in candidates if (m.get("size_bytes", float("inf")) / (1024 * 1024)) <= ram_budget_mb]
    if not fits:
        return None
    code_capable = [
        m
        for m in fits
        if CODE_NAME_PATTERN.search(
            " ".join(filter(None, [m.get("key"), m.get("display_name"), m.get("architecture")]))
        )
    ]
    pool = code_capable if code_capable else fits
    pool.sort(key=lambda m: -m.get("size_bytes", 0))
    return pool[0]


async def _load_and_warmup_lmstudio_in_background(model_key: str) -> None:
    global _provisioning_task, _provisioning_model_id, _provisioning_started_at
    try:
        async with httpx.AsyncClient(timeout=MODEL_PROVISION_TIMEOUT_SECONDS) as client:
            try:
                resp = await client.post(
                    f"{LOCAL_CONTROL_URL}/api/v1/models/load",
                    json={"model": model_key},
                    timeout=MODEL_PROVISION_TIMEOUT_SECONDS,
                )
                if resp.status_code >= 400:
                    logger.error(
                        "failed to load LM Studio model %s: HTTP %s", model_key, resp.status_code
                    )
                    return
            except httpx.HTTPError as exc:
                logger.error("failed to load LM Studio model %s: %s", model_key, exc)
                return
            await _warmup_model(client, model_key)
            logger.info("%s is now loaded and warm", model_key)
    finally:
        _provisioning_task = None
        _provisioning_model_id = None
        _provisioning_started_at = None


async def _resolve_lmstudio_model(client: httpx.AsyncClient, requested_model: str) -> tuple[str | None, str]:
    global _provisioning_task, _provisioning_model_id, _provisioning_started_at

    models = await _get_lmstudio_models(client) or []
    loaded = [m for m in models if m.get("loaded_instances")]
    warm_model = next((n for m in loaded if (n := _lmstudio_model_name(m)) in _warmed_model_ids), None)
    if warm_model:
        return warm_model, f"using already-running local model {warm_model}"

    if _provisioning_task is not None and not _provisioning_task.done():
        elapsed = time.monotonic() - (_provisioning_started_at or time.monotonic())
        return None, (
            f"local model {_provisioning_model_id} is still loading/warming "
            f"in the background ({elapsed:.0f}s so far) — falling back for "
            f"this request"
        )

    if not AUTO_PROVISION_MODEL:
        return None, "no running local model and AUTO_PROVISION_MODEL is disabled"

    async with _provision_lock:
        models = await _get_lmstudio_models(client) or []
        loaded = [m for m in models if m.get("loaded_instances")]
        warm_model = next((n for m in loaded if (n := _lmstudio_model_name(m)) in _warmed_model_ids), None)
        if warm_model:
            return warm_model, f"using already-running local model {warm_model}"
        if _provisioning_task is not None and not _provisioning_task.done():
            elapsed = time.monotonic() - (_provisioning_started_at or time.monotonic())
            return None, (
                f"local model {_provisioning_model_id} is still loading/warming "
                f"in the background ({elapsed:.0f}s so far) — falling back for "
                f"this request"
            )

        loaded_name = _lmstudio_model_name(loaded[0]) if loaded else None
        if loaded_name:
            logger.info(
                "found loaded-but-unwarmed LM Studio model %s; warming it up in the background",
                loaded_name,
            )
            _provisioning_model_id = loaded_name
            _provisioning_started_at = time.monotonic()
            _provisioning_task = asyncio.create_task(_warmup_only_in_background(loaded_name))
            return None, (
                f"local model {loaded_name} is loaded but not yet proven "
                f"warm; warming it up in the background — falling back for "
                f"this request"
            )

        ram_mb = get_free_ram_mb()
        ram_budget = max(0.0, ram_mb - MODEL_RAM_HEADROOM_MB)
        chosen = _pick_best_lmstudio_model(models, ram_budget)
        if chosen is None:
            return None, (
                f"no downloaded LM Studio model fits available RAM (free "
                f"{ram_mb:.0f}MB, budget after headroom {ram_budget:.0f}MB) "
                f"— download a smaller model or free up memory"
            )

        model_id = _lmstudio_model_name(chosen)
        logger.info(
            "loading and warming up downloaded LM Studio model %s (%.0fMB, code_named=%s)",
            model_id,
            chosen.get("size_bytes", 0) / (1024 * 1024),
            bool(CODE_NAME_PATTERN.search(model_id or "")),
        )
        _provisioning_model_id = model_id
        _provisioning_started_at = time.monotonic()
        _provisioning_task = asyncio.create_task(_load_and_warmup_lmstudio_in_background(model_id))
        return None, (
            f"no LM Studio model was loaded; loading and warming up "
            f"{model_id} in the background — falling back for this request"
        )


# --- vLLM / llama.cpp backends ----------------------------------------------
#
# Both are commonly deployed with a single model fixed at process launch
# (`vllm serve <model>`, `llama-server -m <path>`) — the model finishes
# loading (including any GPU warmup/graph capture) before the HTTP server
# even starts accepting connections, so there's nothing to pick, provision,
# or wait for: whatever /v1/models reports is already the answer, already
# warm. (llama-server's newer multi-model "router mode" with per-request
# auto-loading isn't handled here — this covers the common single-model
# deployment for both.)


async def _resolve_fixed_single_model(client: httpx.AsyncClient, backend_label: str) -> tuple[str | None, str]:
    try:
        resp = await client.get(
            f"{LOCAL_ENDPOINT}/models",
            headers={"Authorization": f"Bearer {LOCAL_API_KEY}"},
            timeout=HEALTH_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except (httpx.HTTPError, ValueError):
        return None, f"could not read the loaded model from {backend_label}'s /v1/models"
    if not data:
        return None, f"{backend_label} reported no loaded model"
    model_id = data[0].get("id")
    if not model_id:
        return None, f"{backend_label} reported a model with no id"
    _warmed_model_ids.add(model_id)
    return model_id, f"using {backend_label}'s loaded model {model_id}"


# --- Usage tracking -----------------------------------------------------------
#
# Local routing's whole value proposition is savings the caller can't see
# unless something measures it. This logs every routed completion (local or
# cloud-upstream) to a small local SQLite file and exposes GET /stats to
# turn it into a number — "how much have I actually saved" — rather than
# leaving that invisible.


def _init_usage_db() -> None:
    os.makedirs(os.path.dirname(USAGE_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(USAGE_DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                router_decision TEXT NOT NULL,
                backend TEXT,
                model TEXT,
                task_type TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _log_usage(
    router_decision: str,
    backend: str | None,
    model: str | None,
    task_type: str | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> None:
    try:
        conn = sqlite3.connect(USAGE_DB_PATH)
        try:
            conn.execute(
                "INSERT INTO usage_log "
                "(ts, router_decision, backend, model, task_type, prompt_tokens, completion_tokens) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (time.time(), router_decision, backend, model, task_type, prompt_tokens, completion_tokens),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        # Usage tracking is best-effort — never let a logging failure break
        # an otherwise-successful completion response.
        logger.warning("usage logging failed (continuing anyway): %s", exc)


_WINDOW_SECONDS = {"today": 86400, "week": 7 * 86400, "all": None}


def _compute_stats(window: str) -> dict[str, Any]:
    since = None
    seconds = _WINDOW_SECONDS.get(window, _WINDOW_SECONDS["all"])
    if seconds is not None:
        since = time.time() - seconds

    conn = sqlite3.connect(USAGE_DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        where = "WHERE ts >= ?" if since is not None else ""
        params: tuple[Any, ...] = (since,) if since is not None else ()

        by_decision: dict[str, dict[str, int]] = {}
        for row in conn.execute(
            f"SELECT router_decision, COUNT(*) AS n, "
            f"COALESCE(SUM(prompt_tokens), 0) AS ptok, "
            f"COALESCE(SUM(completion_tokens), 0) AS ctok "
            f"FROM usage_log {where} GROUP BY router_decision",
            params,
        ):
            by_decision[row["router_decision"]] = {
                "requests": row["n"],
                "prompt_tokens": row["ptok"],
                "completion_tokens": row["ctok"],
            }

        by_backend_model: list[dict[str, Any]] = []
        for row in conn.execute(
            f"SELECT backend, model, COUNT(*) AS n, "
            f"COALESCE(SUM(prompt_tokens), 0) AS ptok, "
            f"COALESCE(SUM(completion_tokens), 0) AS ctok "
            f"FROM usage_log {where} AND router_decision = 'local' GROUP BY backend, model"
            if where
            else "SELECT backend, model, COUNT(*) AS n, "
            "COALESCE(SUM(prompt_tokens), 0) AS ptok, "
            "COALESCE(SUM(completion_tokens), 0) AS ctok "
            "FROM usage_log WHERE router_decision = 'local' GROUP BY backend, model",
            params,
        ):
            by_backend_model.append(
                {
                    "backend": row["backend"],
                    "model": row["model"],
                    "requests": row["n"],
                    "prompt_tokens": row["ptok"],
                    "completion_tokens": row["ctok"],
                }
            )
    finally:
        conn.close()

    local = by_decision.get("local", {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0})
    estimated_usd_saved = (
        local["prompt_tokens"] / 1_000_000 * CLOUD_INPUT_COST_PER_MTOK
        + local["completion_tokens"] / 1_000_000 * CLOUD_OUTPUT_COST_PER_MTOK
    )

    return {
        "window": window,
        "by_router_decision": by_decision,
        "local_by_backend_and_model": by_backend_model,
        "estimated_usd_saved": round(estimated_usd_saved, 4),
        "cost_estimate_basis": {
            "note": (
                "Estimate only — assumes every locally-routed request would "
                "otherwise have cost this much on a cloud model. Set "
                "CLOUD_INPUT_COST_PER_MTOK / CLOUD_OUTPUT_COST_PER_MTOK to "
                "your actual plan's effective rate for a meaningful number."
            ),
            "input_cost_per_mtok_usd": CLOUD_INPUT_COST_PER_MTOK,
            "output_cost_per_mtok_usd": CLOUD_OUTPUT_COST_PER_MTOK,
        },
    }


# --- HTTP handlers ----------------------------------------------------------

app = FastAPI(
    title="hybrid-local-router proxy",
    description=(
        "Routes boilerplate/refactor chat completion requests to a local "
        "OpenAI-compatible LLM cluster, auto-managing which model is loaded "
        "when talking to Exo; passes everything else through."
    ),
    version="1.1.0",
)


@app.on_event("startup")
async def _on_startup() -> None:
    _init_usage_db()


@app.get("/stats")
async def stats(window: str = "all") -> dict[str, Any]:
    """Usage summary: requests by routing decision, local usage broken down
    by backend/model, and an estimated $ saved by routing locally instead
    of to a cloud model. window: 'today' | 'week' | 'all' (default)."""
    if window not in _WINDOW_SECONDS:
        return {
            "error": {
                "message": f"Invalid window {window!r}. Use one of: {sorted(_WINDOW_SECONDS)}.",
                "type": "invalid_window",
            }
        }
    return _compute_stats(window)


@app.get("/health")
async def health() -> dict[str, Any]:
    """Report proxy status, local reachability, detected backend, capacity,
    and (for Exo/Ollama) any currently active local model."""
    local_status = await _check_local_cluster()
    info: dict[str, Any] = {
        "status": "ok",
        "local_endpoint": LOCAL_ENDPOINT,
        "local_cluster_reachable": local_status,
        "cloud_upstream_configured": CLOUD_UPSTREAM_URL is not None,
        "auto_provision_model": AUTO_PROVISION_MODEL,
        "free_ram_mb": round(get_free_ram_mb()),
        "free_disk_mb": round(get_free_disk_mb(MODEL_CACHE_PATH)),
    }
    if _provisioning_task is not None and not _provisioning_task.done():
        info["provisioning_in_progress"] = {
            "model": _provisioning_model_id,
            "elapsed_seconds": round(time.monotonic() - (_provisioning_started_at or time.monotonic())),
        }
    async with httpx.AsyncClient() as client:
        backend = await _detect_backend(client)
        info["backend"] = backend
        if backend == "exo":
            state = await _get_exo_state(client)
            if state is not None:
                info["active_local_models"] = sorted({mid for _, mid in _instance_model_ids(state)})
                info["loaded_but_unwarmed_model"] = _find_ready_local_model(state)
                info["ready_local_model"] = _find_warm_local_model(state)
        elif backend == "ollama":
            running = await _get_ollama_running(client) or []
            names = sorted({n for m in running if (n := (m.get("name") or m.get("model")))})
            info["active_local_models"] = names
            info["ready_local_model"] = next((n for n in names if n in _warmed_model_ids), None)
            info["loaded_but_unwarmed_model"] = next((n for n in names if n not in _warmed_model_ids), None)
        elif backend == "lmstudio":
            models = await _get_lmstudio_models(client) or []
            names = sorted({n for m in models if m.get("loaded_instances") and (n := _lmstudio_model_name(m))})
            info["active_local_models"] = names
            info["ready_local_model"] = next((n for n in names if n in _warmed_model_ids), None)
            info["loaded_but_unwarmed_model"] = next((n for n in names if n not in _warmed_model_ids), None)
        elif backend in ("vllm", "llamacpp"):
            model_id, _note = await _resolve_fixed_single_model(client, backend)
            info["active_local_models"] = [model_id] if model_id else []
            info["ready_local_model"] = model_id
            info["loaded_but_unwarmed_model"] = None
    return info


async def _check_local_cluster() -> bool:
    try:
        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT_SECONDS) as client:
            resp = await client.get(
                f"{LOCAL_ENDPOINT}/models",
                headers={"Authorization": f"Bearer {LOCAL_API_KEY}"},
            )
            return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError):
        return False


def _pass_through_response(reason: str) -> Response:
    return JSONResponse(
        status_code=200,
        content={
            "pass_through": True,
            "router_decision": "pass_through",
            "router_reason": reason,
            "message": (
                "This request was not offloaded to the local cluster. "
                "No CLOUD_UPSTREAM_URL is configured, so no completion was "
                "generated. Send this request to your primary cloud model "
                "instead."
            ),
        },
    )


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    body = await request.json()
    try:
        payload = ChatCompletionRequest.model_validate(body)
    except Exception as exc:  # noqa: BLE001 - surface validation errors to caller
        return JSONResponse(
            status_code=400,
            content={"error": {"message": str(exc), "type": "invalid_request"}},
        )

    if payload.stream:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": (
                        "This proxy does not support streaming responses. "
                        "Set stream=false (or omit it) and request the "
                        "complete output in one response."
                    ),
                    "type": "streaming_not_supported",
                }
            },
        )

    decision, reason = classify_task(payload)
    logger.info("routing decision=%s reason=%r model=%s", decision, reason, payload.model)

    if decision == "local":
        return await _forward_to_local(payload, reason)

    if decision == "cloud_upstream":
        return await _forward_to_cloud_upstream(payload, reason)

    return _pass_through_response(reason)


async def _forward_to_local(payload: ChatCompletionRequest, reason: str) -> Response:
    outgoing = payload.model_dump(exclude={"task_type"}, exclude_none=True)

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        model_id, note = await _resolve_local_model(client, payload.model)
        if model_id is None:
            logger.warning("no usable local model: %s", note)
            combined_reason = f"{reason}; local unavailable ({note})"
            if CLOUD_UPSTREAM_URL:
                return await _forward_to_cloud_upstream(payload, combined_reason)
            return _pass_through_response(combined_reason)

        outgoing["model"] = model_id
        reason = f"{reason}; {note}"

        # Offload candidates are mechanical (boilerplate/refactor/codegen):
        # the caller already did the design thinking in the architect phase,
        # so a local "thinking" model burning its token budget on chain-of-
        # thought before answering only adds latency without adding value.
        # Field names differ by backend (see _thinking_suppression_fields).
        # Callers can still override any of these.
        backend = await _detect_backend(client)
        for key, value in _thinking_suppression_fields(backend).items():
            outgoing.setdefault(key, value)

        try:
            resp = await client.post(
                f"{LOCAL_ENDPOINT}/chat/completions",
                json=outgoing,
                headers={
                    "Authorization": f"Bearer {LOCAL_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
        except httpx.ConnectError:
            logger.warning("local cluster offline at %s", LOCAL_ENDPOINT)
            return JSONResponse(
                status_code=502,
                content={
                    "error": {
                        "message": (
                            f"Local cluster at {LOCAL_ENDPOINT} is unreachable. "
                            "Fall back to the cloud model for this request."
                        ),
                        "type": "local_cluster_offline",
                    },
                    "router_decision": "local",
                    "router_reason": reason,
                },
            )
        except httpx.TimeoutException:
            logger.warning("local cluster timed out at %s", LOCAL_ENDPOINT)
            return JSONResponse(
                status_code=504,
                content={
                    "error": {
                        "message": f"Local cluster at {LOCAL_ENDPOINT} timed out after {REQUEST_TIMEOUT_SECONDS}s.",
                        "type": "local_cluster_timeout",
                    },
                    "router_decision": "local",
                    "router_reason": reason,
                },
            )
        except httpx.HTTPError as exc:
            logger.error("local cluster request failed: %s", exc)
            return JSONResponse(
                status_code=502,
                content={
                    "error": {"message": str(exc), "type": "local_cluster_error"},
                    "router_decision": "local",
                    "router_reason": reason,
                },
            )

        if resp.status_code >= 400:
            logger.warning("local cluster returned status %s", resp.status_code)
            return JSONResponse(
                status_code=resp.status_code,
                content={
                    "error": {
                        "message": f"Local cluster returned HTTP {resp.status_code}.",
                        "type": "local_cluster_http_error",
                        "body": _safe_json(resp),
                    },
                    "router_decision": "local",
                    "router_reason": reason,
                },
            )

        _warmed_model_ids.add(model_id)
        data = resp.json()
        data["router_decision"] = "local"
        data["router_reason"] = reason
        usage = data.get("usage", {})
        _log_usage(
            router_decision="local",
            backend=backend,
            model=model_id,
            task_type=payload.task_type,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )
        return JSONResponse(status_code=200, content=data)


async def _forward_to_cloud_upstream(payload: ChatCompletionRequest, reason: str) -> Response:
    assert CLOUD_UPSTREAM_URL is not None
    outgoing = payload.model_dump(exclude={"task_type"}, exclude_none=True)
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{CLOUD_UPSTREAM_URL}/chat/completions",
                json=outgoing,
                headers={
                    "Authorization": f"Bearer {CLOUD_UPSTREAM_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as exc:
        logger.error("cloud upstream request failed: %s", exc)
        return JSONResponse(
            status_code=502,
            content={
                "error": {"message": str(exc), "type": "cloud_upstream_error"},
                "router_decision": "cloud_upstream",
                "router_reason": reason,
            },
        )

    data = _safe_json(resp)
    if resp.status_code >= 400:
        return JSONResponse(status_code=resp.status_code, content=data)

    data["router_decision"] = "cloud_upstream"
    data["router_reason"] = reason
    usage = data.get("usage", {}) if isinstance(data, dict) else {}
    _log_usage(
        router_decision="cloud_upstream",
        backend=None,
        model=payload.model,
        task_type=payload.task_type,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
    )
    return JSONResponse(status_code=200, content=data)


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return {"raw_body": resp.text}


if __name__ == "__main__":
    import uvicorn

    logger.info(
        "starting hybrid-local-router proxy on %s:%d (local_endpoint=%s, "
        "exo_control_url=%s, auto_provision=%s, cloud_upstream=%s)",
        ROUTER_HOST,
        ROUTER_PORT,
        LOCAL_ENDPOINT,
        LOCAL_CONTROL_URL,
        AUTO_PROVISION_MODEL,
        CLOUD_UPSTREAM_URL or "<not configured, pass_through mode>",
    )
    uvicorn.run(app, host=ROUTER_HOST, port=ROUTER_PORT)
