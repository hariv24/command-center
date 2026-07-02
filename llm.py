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
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
TOGETHER_BASE_URL = "https://api.together.xyz/v1"

# Tier -> model per provider. OpenRouter free tier bills per request, not per
# token, so the heavy tier can be as large as available.
MODELS = {
    "openrouter": {
        "heavy": os.getenv("LLM_MODEL_HEAVY", "nvidia/nemotron-3-ultra-550b-a55b:free"),
        "fast": os.getenv("LLM_MODEL_FAST", "openai/gpt-oss-20b:free"),
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


def _is_rate_limit(e):
    err = str(e).lower()
    return any(s in err for s in ("rate_limit", "429", "quota", "limit reached", "tokens per"))


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


async def _try(client, model, messages, max_tokens, temperature):
    r = await client.chat.completions.create(
        model=model, messages=messages,
        max_tokens=max_tokens, temperature=temperature,
    )
    text = r.choices[0].message.content
    if not text or not text.strip():
        raise RuntimeError(f"empty response from {model}")
    return text


async def call_llm(messages, tier="heavy", max_tokens=900, temperature=0.85):
    """
    messages: standard chat messages array [{"role": ..., "content": ...}, ...]
    tier: "heavy" (advisor responses, synthesis) or "fast" (routing, extraction)
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
                return await _try(client, model, messages, max_tokens, temperature)
            except Exception:
                continue  # any failure -> next free model, then next provider
        return None

    async def _try_groq():
        for client in _groq_clients():
            try:
                return await _try(client, MODELS["groq"][tier], messages, max_tokens, temperature)
            except Exception as e:
                if _is_rate_limit(e):
                    continue
                raise
        return None

    # Fast tier: Groq first — small token cost, rock-solid JSON compliance,
    # and it keeps OpenRouter's daily request budget for heavy advisor calls.
    # Heavy tier: OpenRouter first — far stronger free models, per-request billing.
    if provider == "openrouter":
        order = [_try_groq, _try_openrouter] if tier == "fast" else [_try_openrouter, _try_groq]
    else:
        order = [_try_groq, _try_openrouter]
    for attempt in order:
        result = await attempt()
        if result:
            return result
    groq_clients = _groq_clients()

    # 3. Together.ai
    together = _together_client()
    if together:
        try:
            return await _try(together, MODELS["together"][tier], messages, max_tokens, temperature)
        except Exception:
            pass

    # 4. Last resort — Groq fast model (smaller daily budget cost)
    if tier == "heavy" and groq_clients:
        try:
            return await _try(groq_clients[0], MODELS["groq"]["fast"], messages, max_tokens, temperature)
        except Exception:
            pass

    raise RuntimeError("rate_limit_exceeded")
