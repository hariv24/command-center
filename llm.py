"""
Single entry point for every LLM call in the command center.

Provider chain: OpenRouter (free models) -> Groq keys -> Together.ai.
Configure via .env:
    LLM_PROVIDER=openrouter          # or groq / together
    OPENROUTER_API_KEY=...
    LLM_MODEL_HEAVY=nvidia/nemotron-3-ultra-550b-a55b:free
    LLM_MODEL_FAST=nvidia/nemotron-3-nano-30b-a3b:free
"""

import os
import time
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

logger = logging.getLogger("llm")

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
TOGETHER_BASE_URL = "https://api.together.xyz/v1"

# Tier -> model per provider. OpenRouter free tier bills per request, not per
# token, so the heavy tier can be as large as available.
MODELS = {
    "openrouter": {
        "heavy": os.getenv("LLM_MODEL_HEAVY", "nvidia/nemotron-3-ultra-550b-a55b:free"),
        # gpt-oss-20b spends nearly its whole token budget on hidden reasoning
        # even with reasoning.exclude set (measured: 47 of 50 max_tokens on a
        # trivial prompt), so it reliably returns empty content on the low
        # max_tokens fast-tier calls use for routing/extraction. nemotron-3-nano
        # has the same reasoning overhead but actually finishes (finish_reason
        # "stop", not "length") and matches the nemotron-3 family already used
        # for the heavy tier.
        "fast": os.getenv("LLM_MODEL_FAST", "nvidia/nemotron-3-nano-30b-a3b:free"),
        # Tried in order if the tier model errors (removed model, upstream 429...)
        "heavy_backups": [
            "nousresearch/hermes-3-llama-3.1-405b:free",
            "openai/gpt-oss-120b:free",
            "meta-llama/llama-3.3-70b-instruct:free",
        ],
        "fast_backups": [
            "meta-llama/llama-3.3-70b-instruct:free",
            "google/gemma-4-31b-it:free",
        ],
    },
    "groq": {
        "heavy": "llama-3.3-70b-versatile",
        "fast": "llama-3.1-8b-instant",
    },
    "together": {
        "heavy": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "fast": "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
    },
}


def _openrouter_client():
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        return None
    try:
        from openai import AsyncOpenAI
        return AsyncOpenAI(api_key=key, base_url=OPENROUTER_BASE_URL)
    except ImportError:
        return None


def _groq_clients():
    clients = []
    try:
        from groq import AsyncGroq
    except ImportError:
        return clients
    for var in ("GROQ_API_KEY", "GROQ_API_KEY_2", "GROQ_API_KEY_3"):
        key = os.getenv(var)
        if key:
            clients.append(AsyncGroq(api_key=key))
    return clients


def _together_client():
    key = os.getenv("TOGETHER_API_KEY")
    if not key:
        return None
    try:
        from openai import AsyncOpenAI
        return AsyncOpenAI(api_key=key, base_url=TOGETHER_BASE_URL)
    except ImportError:
        return None


import re as _re

_THINK_RE = _re.compile(r"<think>.*?</think>\s*", _re.DOTALL)

# Weaker/free reasoning models sometimes narrate the instruction instead of
# following it ("The user is asking me to react to what X said... Let me
# analyze...") even when explicitly told not to. Strip any leading paragraph
# that reads like task-narration rather than an actual answer — iteratively,
# since some responses stack two or three of these before the real content
# starts. The length cap keeps this from ever eating a legitimate paragraph
# that just happens to mention "the user" in passing.
_NARRATION_MARKERS = (
    "the user is asking", "the user wants", "the user just",
    "i need to state", "i need to analyze", "i need to respond", "i need to react",
    "let me analyze", "let me think", "let me respond", "let me address",
    "i'll analyze", "i will analyze", "i'll respond to", "i will respond to",
    "i should respond", "i should analyze", "i should react",
)


def _strip_meta_narration(text):
    while True:
        parts = text.split("\n\n", 1)
        if len(parts) != 2:
            return text
        first, rest = parts
        if len(first) < 400 and any(m in first.lower() for m in _NARRATION_MARKERS):
            text = rest.lstrip()
        else:
            return text


async def _try(client, model, messages, max_tokens, temperature, provider_name, openrouter=False, timeout=60):
    kwargs = dict(model=model, messages=messages,
                  max_tokens=max_tokens, temperature=temperature,
                  timeout=timeout)  # a stalled provider must fall through, not hang the board
    if openrouter:
        # Reasoning models (Nemotron, gpt-oss) otherwise leak their hidden
        # thinking into content or burn the whole token budget on it.
        kwargs["extra_body"] = {"reasoning": {"exclude": True}}
    t0 = time.monotonic()
    r = await client.chat.completions.create(**kwargs)
    latency_ms = round((time.monotonic() - t0) * 1000)
    # A malformed/error response from a free model can come back with an empty
    # or missing choices list (seen live: "'NoneType' object is not
    # subscriptable" from indexing choices[0] directly) — treat that the same
    # as an empty response so it falls through to the next model instead of
    # throwing an unhandled TypeError that the caller doesn't expect.
    if not getattr(r, "choices", None):
        raise RuntimeError(f"no choices in response from {model}")
    text = r.choices[0].message.content
    if text:
        text = _THINK_RE.sub("", text)
        text = _strip_meta_narration(text)
    # A response can be technically non-empty but garbage (a model that stops
    # after emitting a couple of stray markdown characters, e.g. "**") — treat
    # anything under 15 real characters the same as empty so it falls through
    # to the next model in the chain instead of shipping a broken 2-char reply.
    if not text or len(text.strip()) < 15:
        raise RuntimeError(f"empty response from {model}")
    finish_reason = getattr(r.choices[0], "finish_reason", None)
    truncated = finish_reason == "length"
    if truncated:
        # The model ran out of room mid-generation — this is the #1 cause of
        # "cut off in half" / broken markdown bug reports. Surfacing it loudly
        # here so it shows up in logs instead of silently shipping half a brief.
        logger.warning(
            f"llm_call TRUNCATED provider={provider_name} model={model} "
            f"max_tokens={max_tokens} chars={len(text)} — raise max_tokens for this call"
        )
    logger.info(f"llm_call provider={provider_name} model={model} latency_ms={latency_ms} chars={len(text)} finish_reason={finish_reason}")
    return text, {"provider": provider_name, "model": model, "latency_ms": latency_ms, "truncated": truncated}


async def call_llm(messages, tier="heavy", max_tokens=900, temperature=0.85, return_meta=False, timeout=60):
    """
    messages: standard chat messages array [{"role": ..., "content": ...}, ...]
    tier: "heavy" (advisor responses, synthesis) or "fast" (routing, extraction)
    return_meta: if True, returns (text, {"provider", "model", "latency_ms"}) instead of just text
    timeout: per-request seconds before falling through to the next model/provider.
        Keep this short (default 60s) for anything interactive — the board must
        stay responsive. Background jobs nobody is watching live (daily brief,
        weekly synthesis) can pass a much longer timeout since a slow-but-
        successful big generation shouldn't be killed just to "fail fast".
    Raises RuntimeError("rate_limit_exceeded") only when every provider fails.
    """
    provider = os.getenv("LLM_PROVIDER", "openrouter")

    async def _try_openrouter():
        client = _openrouter_client()
        if not client:
            return None
        candidates = [MODELS["openrouter"][tier]] + MODELS["openrouter"].get(f"{tier}_backups", [])
        for model in candidates:
            try:
                return await _try(client, model, messages, max_tokens, temperature, "openrouter", openrouter=True, timeout=timeout)
            except Exception:
                continue  # any failure -> next free model, then next provider
        return None

    async def _try_groq():
        # Any failure -> next groq key, then fall through to the next provider
        # in `order` below. This used to re-raise non-rate-limit errors, which
        # aborted the whole call_llm() attempt instead of falling through —
        # defeating the fallback chain for anything that wasn't a recognized
        # "rate_limit"/"429"/"quota" string (e.g. the empty/garbage-response
        # check below, or any transient API hiccup). The docstring promises
        # "raises only when every provider fails" — this makes that true.
        for client in _groq_clients():
            try:
                return await _try(client, MODELS["groq"][tier], messages, max_tokens, temperature, "groq", timeout=timeout)
            except Exception:
                continue
        return None

    # OpenRouter first for both tiers — stronger free models, a 1000/day budget
    # (vs Groq's much tighter free-tier caps), and per-request rather than
    # per-token billing. Fast tier used to try Groq first on the theory that it
    # kept OpenRouter's daily budget free for heavy advisor calls, but at
    # 1000/day that headroom doesn't matter, and it meant every board turn's
    # routing/synthesis/extraction calls were burning Groq's tighter budget
    # for no real benefit. Groq is now purely the fallback for both tiers.
    if provider == "openrouter":
        order = [_try_openrouter, _try_groq]
    else:
        order = [_try_groq, _try_openrouter]
    for attempt in order:
        result = await attempt()
        if result:
            return result if return_meta else result[0]
    groq_clients = _groq_clients()

    # 3. Together.ai
    together = _together_client()
    if together:
        try:
            result = await _try(together, MODELS["together"][tier], messages, max_tokens, temperature, "together", timeout=timeout)
            return result if return_meta else result[0]
        except Exception:
            pass

    # 4. Last resort — Groq fast model (smaller daily budget cost)
    if tier == "heavy" and groq_clients:
        try:
            result = await _try(groq_clients[0], MODELS["groq"]["fast"], messages, max_tokens, temperature, "groq", timeout=timeout)
            return result if return_meta else result[0]
        except Exception:
            pass

    raise RuntimeError("rate_limit_exceeded")


async def _try_stream(client, model, messages, max_tokens, temperature, provider_name, openrouter=False):
    """
    Yields (delta_text, None) chunks as they arrive, then a final
    (None, meta) tuple. Raises before yielding anything if the request itself
    fails to start (so the caller can fall back to the next model cleanly);
    once tokens have started flowing we commit to this stream — a mid-stream
    error just ends it early rather than restarting from scratch.
    """
    kwargs = dict(model=model, messages=messages, max_tokens=max_tokens,
                  temperature=temperature, timeout=60, stream=True)
    if openrouter:
        kwargs["extra_body"] = {"reasoning": {"exclude": True}}
    t0 = time.monotonic()
    stream = await client.chat.completions.create(**kwargs)
    started = False
    full_text = []
    think_open = False
    async for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if not delta:
            continue
        started = True
        # Strip <think>...</think> spans even when they straddle chunks.
        if think_open:
            if "</think>" in delta:
                delta = delta.split("</think>", 1)[1]
                think_open = False
            else:
                continue
        if "<think>" in delta:
            before, _, after = delta.partition("<think>")
            if before:
                full_text.append(before)
                yield before, None
            if "</think>" in after:
                after = after.split("</think>", 1)[1]
            else:
                think_open = True
                after = ""
            delta = after
        if delta:
            full_text.append(delta)
            yield delta, None
    latency_ms = round((time.monotonic() - t0) * 1000)
    text = "".join(full_text)
    if not started or len(text.strip()) < 15:
        raise RuntimeError(f"empty stream from {model}")
    # Can't un-send already-streamed tokens, but the *stored* text (session
    # history, the final "done" event the UI re-renders from) comes from this
    # cleaned version — see _strip_meta_narration's docstring.
    text = _strip_meta_narration(text)
    logger.info(f"llm_stream provider={provider_name} model={model} latency_ms={latency_ms} chars={len(text)}")
    yield None, {"provider": provider_name, "model": model, "latency_ms": latency_ms, "text": text}


async def stream_llm(messages, tier="heavy", max_tokens=900, temperature=0.85):
    """
    Async generator version of call_llm. Yields text deltas as they arrive;
    the final yielded item is (None, meta) with the full text + provenance.
    Falls back across providers/models the same way call_llm does, but only
    before any tokens have been emitted — once a model starts streaming we
    stay with it rather than risk a duplicated partial response.
    """
    provider = os.getenv("LLM_PROVIDER", "openrouter")

    async def _candidates_openrouter():
        client = _openrouter_client()
        if not client:
            return
        for model in [MODELS["openrouter"][tier]] + MODELS["openrouter"].get(f"{tier}_backups", []):
            yield client, model, "openrouter", True

    async def _candidates_groq():
        for client in _groq_clients():
            yield client, MODELS["groq"][tier], "groq", False

    # OpenRouter first for both tiers, matching call_llm — see its comment for
    # why (1000/day budget, better free models, per-request billing).
    if provider == "openrouter":
        sources = [_candidates_openrouter, _candidates_groq]
    else:
        sources = [_candidates_groq, _candidates_openrouter]

    for source in sources:
        async for client, model, provider_name, is_or in source():
            try:
                async for delta, meta in _try_stream(client, model, messages, max_tokens, temperature, provider_name, openrouter=is_or):
                    yield delta, meta
                return  # stream completed successfully
            except Exception:
                # Any failure -> next candidate, then the non-streaming last
                # resort below. This used to re-raise non-rate-limit Groq
                # errors, aborting the whole generator instead of falling
                # through — the same bug fixed in call_llm's _try_groq, just
                # in the streaming path that every interactive board response
                # actually goes through.
                continue

    # Last resort — non-streaming call, emitted as a single chunk so callers
    # that only understand the streaming protocol still get an answer.
    text, meta = await call_llm(messages, tier=tier, max_tokens=max_tokens, temperature=temperature, return_meta=True)
    yield text, None
    yield None, {**meta, "text": text}
