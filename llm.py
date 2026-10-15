# -*- coding: utf-8 -*-
"""
Provider-agnostic LLM client (OpenAI-compatible HTTP, no SDK dependency).

Four providers, auto-detected from the API-key prefix so callers never have to
care which one is in use:

  * Groq        key ``gsk_…``    - fast, free, but tight rate limits.
  * OpenRouter  key ``sk-or…``   - your own credits, no free-tier throttling.
  * Anthropic   key ``sk-ant…``  - Claude models, via Anthropic's OpenAI-
                                   compatible Chat Completions endpoint.
  * Gemini      key ``AIza…``    - Google Gemini, via its OpenAI-compatible
                                   endpoint.

All four expose an OpenAI-style ``/chat/completions`` and ``/models``, so one
code path serves them; only the base URL, auth headers, and the max-tokens
field name differ.

In SEO Audit Pro the LLM is used only for the OPTIONAL AI-written executive
summary. Everything else works with no key at all.
"""

import json
import re
import time

import requests

# ---- provider bases --------------------------------------------------------
GROQ_BASE = "https://api.groq.com/openai/v1"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
ANTHROPIC_BASE = "https://api.anthropic.com/v1"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"

_BASE = {
    "groq": GROQ_BASE,
    "openrouter": OPENROUTER_BASE,
    "anthropic": ANTHROPIC_BASE,
    "gemini": GEMINI_BASE,
}

# Order shown in the UI dropdown, plus their human labels.
PROVIDERS = ["groq", "openrouter", "anthropic", "gemini"]
PROVIDER_LABELS = {
    "groq": "Groq (free · rate-limited)",
    "openrouter": "OpenRouter (your credits)",
    "anthropic": "Anthropic (Claude)",
    "gemini": "Google Gemini",
}
# Environment-variable name each provider's key loads from / saves to.
ENV_KEYS = {
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

# Per-provider default model — all cheap-or-free and reliable at structured JSON.
DEFAULT_MODELS = {
    "groq": "llama-3.3-70b-versatile",
    "openrouter": "openai/gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-latest",
    "gemini": "gemini-2.0-flash",
}

# Suggested models per provider, shown in the app's dropdown.
SUGGESTED_MODELS = {
    "groq": ["llama-3.3-70b-versatile", "openai/gpt-oss-120b",
             "openai/gpt-oss-20b", "llama-3.1-8b-instant"],
    "openrouter": ["openai/gpt-4o-mini", "google/gemini-2.0-flash-001",
                   "anthropic/claude-3.5-haiku",
                   "meta-llama/llama-3.3-70b-instruct",
                   "deepseek/deepseek-chat", "openai/gpt-4o"],
    "anthropic": ["claude-3-5-haiku-latest", "claude-3-5-sonnet-latest",
                  "claude-3-7-sonnet-latest", "claude-sonnet-4-20250514"],
    "gemini": ["gemini-2.0-flash", "gemini-2.0-flash-lite",
               "gemini-1.5-flash", "gemini-1.5-pro"],
}


def provider_for_key(api_key):
    """Return 'groq' | 'openrouter' | 'anthropic' | 'gemini' from the key
    prefix. Order matters: 'sk-or…' and 'sk-ant…' both begin 'sk-'."""
    k = (api_key or "").strip()
    if k.startswith("sk-or"):
        return "openrouter"
    if k.startswith("sk-ant"):
        return "anthropic"
    if k.startswith("AIza"):
        return "gemini"
    return "groq"


def base_url_for_key(api_key):
    return _BASE[provider_for_key(api_key)]


def default_model(provider):
    return DEFAULT_MODELS.get(provider, DEFAULT_MODELS["groq"])


def default_model_for_key(api_key):
    return default_model(provider_for_key(api_key))


def suggested_models(provider):
    return SUGGESTED_MODELS.get(provider, SUGGESTED_MODELS["groq"])


def provider_label(provider):
    return PROVIDER_LABELS.get(provider, provider)


def _auth_headers(api_key, provider=None):
    provider = provider or provider_for_key(api_key)
    h = {"Authorization": "Bearer %s" % api_key, "Content-Type": "application/json"}
    if provider == "openrouter":
        h["HTTP-Referer"] = "https://mindcob.tools"
        h["X-Title"] = "Mindcob SEO Audit Pro"
    elif provider == "anthropic":
        # Anthropic's native /models wants these; its OpenAI-compatible
        # /chat/completions accepts the Bearer header. Sending all is harmless.
        h["x-api-key"] = api_key
        h["anthropic-version"] = "2023-06-01"
    return h


def list_models(api_key, timeout=20):
    """Return a sorted list of live model ids for the key's provider.
    Empty list on failure (network / bad key)."""
    r = requests.get(
        base_url_for_key(api_key) + "/models",
        headers=_auth_headers(api_key),
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json().get("data", [])
    ids = [m.get("id", "") for m in data if m.get("id")]
    ids = [i.split("/", 1)[1] if i.startswith("models/") else i for i in ids]
    return sorted(set(ids))


def _repair_json(s):
    s = s.replace("“", '"').replace("”", '"')
    s = re.sub(r",\s*([}\]])", r"\1", s)
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", s)
    return s


def _parse_json_blob(content):
    """Pull the first valid JSON object/array out of a model reply."""
    if content is None:
        return None
    s = content.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()
    for candidate in (s, _repair_json(s)):
        try:
            return json.loads(candidate)
        except Exception:
            pass
    for opener, closer in (("{", "}"), ("[", "]")):
        i = s.find(opener)
        j = s.rfind(closer)
        if 0 <= i < j:
            blob = s[i:j + 1]
            for candidate in (blob, _repair_json(blob)):
                try:
                    return json.loads(candidate)
                except Exception:
                    continue
    return {"raw_response": content}


def chat(api_key, model, system, user, timeout=90, max_tokens=1600,
         temperature=0.4, json_mode=True, max_retries=6):
    """Chat completion with rate-limit (429) and 5xx backoff. Returns content."""
    if not api_key:
        raise RuntimeError("No LLM API key set.")
    provider = provider_for_key(api_key)
    base = _BASE[provider]
    headers = _auth_headers(api_key, provider)

    tok_key = "max_completion_tokens" if provider == "groq" else "max_tokens"
    supports_json = provider in ("groq", "openrouter", "gemini")

    def build(use_json):
        p = {"model": model,
             "messages": [{"role": "system", "content": system},
                          {"role": "user", "content": user}],
             "temperature": temperature, tok_key: max_tokens}
        if use_json and supports_json:
            p["response_format"] = {"type": "json_object"}
        return p

    use_json = json_mode
    last_err = "unknown error"
    for attempt in range(max_retries):
        try:
            r = requests.post(base + "/chat/completions", headers=headers,
                              json=build(use_json), timeout=timeout)
        except requests.RequestException as e:
            last_err = "network error: %s" % e
            time.sleep(min(2 * (attempt + 1), 15))
            continue

        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]

        if r.status_code == 429 or r.status_code >= 500:
            try:
                wait = float(r.headers.get("retry-after", ""))
            except (TypeError, ValueError):
                wait = 0.0
            if not wait:
                wait = min(2.0 * (2 ** attempt), 30.0)
            last_err = "%s %d (rate/again); waiting %.0fs" % (
                provider, r.status_code, wait)
            time.sleep(wait)
            continue

        # 4xx other than 429: a model that rejects response_format -> retry once
        if use_json:
            use_json = False
            continue
        try:
            last_err = r.json().get("error", {}).get("message", r.text)
        except Exception:
            last_err = r.text
        raise RuntimeError("%s API %d: %s" % (provider, r.status_code, last_err))

    raise RuntimeError("%s unavailable after %d retries: %s"
                       % (provider, max_retries, last_err))


def chat_json(api_key, model, system, user, **kw):
    """Chat completion that returns parsed JSON (dict or list)."""
    return _parse_json_blob(chat(api_key, model, system, user, **kw))
