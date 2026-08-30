"""Model access with an on-disk replay cache.

Calls are keyed by a hash of their inputs and stored in `agent/cache/`, so a
repeat call replays from disk. Modes:

    live      call the API on a miss, store the result
    replay    cache only, a miss raises
    offline   cache only, falling back to a template

Two providers behind one entry point. Anthropic is the one the architecture
was written against; Gemini is here because that is the key this project has.
`provider="auto"` picks whichever credential is present. The cache key includes
the provider and model, so switching provider does not silently replay another
model's answers, and every cached record says which model wrote it.

Offline output is tagged source="template" so it cannot be mistaken for model
output.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field

ANTHROPIC_MODEL = "claude-opus-5"
GEMINI_MODEL = "gemini-3.6-flash"   # per-model free-tier quota is 20/day;
                                    # gemini-flash-latest aliases 3.7-flash
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")

MAX_RETRIES = 5
RETRY_SLEEP = 8.0
# Gemini 3.x thinks by default and max_output_tokens caps thinking plus visible
# text together. A 600-token budget spent 432 on thinking and returned 54 of
# prose, cut mid-sentence. Give the visible answer its own headroom.
THINKING_HEADROOM = 3000


class CacheMiss(Exception):
    pass


class NoCredentials(Exception):
    pass


def read_env(path: str = ENV_FILE) -> dict:
    """Parse a .env. Tolerates `GEMINI_API KEY=` with a space, which is how the
    key arrived and which the shell would not have loaded."""
    out = {}
    if not os.path.exists(path):
        return out
    for line in open(path, encoding="utf-8"):
        m = re.match(r"\s*([A-Za-z0-9_ ]+?)\s*=\s*(.+?)\s*$", line)
        if m:
            out[m.group(1).replace(" ", "_").upper()] = m.group(2)
    return out


def gemini_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or read_env().get("GEMINI_API_KEY")


def detect_provider() -> str | None:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "anthropic"
    if gemini_key():
        return "gemini"
    return None


@dataclass
class Completion:
    text: str
    source: str            # anthropic | gemini | cache | template
    key: str
    usage: dict = field(default_factory=dict)
    model: str | None = None   # which model wrote it, None for templates

    @property
    def from_model(self) -> bool:
        """True if a model produced this text, now or earlier."""
        return self.source in ("anthropic", "gemini", "cache")

    @property
    def provenance(self) -> str:
        """One short phrase for the screen. A reader should never have to work
        out whether they are looking at model output or a filled-in template."""
        if self.source == "template":
            return "deterministic template, not model output"
        return f"{self.model or 'unknown model'}"


def _gemini_schema(schema: dict) -> dict:
    """Gemini rejects `additionalProperties`, which Anthropic strict mode wants.
    Strip it rather than keeping two copies of every schema."""
    if isinstance(schema, dict):
        return {k: _gemini_schema(v) for k, v in schema.items()
                if k != "additionalProperties"}
    if isinstance(schema, list):
        return [_gemini_schema(v) for v in schema]
    return schema


class LLMClient:
    def __init__(self, mode: str = "offline", cache_dir: str = CACHE_DIR,
                 provider: str = "auto", model: str | None = None):
        if mode not in ("live", "replay", "offline"):
            raise ValueError(f"unknown mode {mode!r}")
        self.mode = mode
        self.cache_dir = cache_dir
        self.provider = detect_provider() or "anthropic" if provider == "auto" else provider
        self.model = model or (GEMINI_MODEL if self.provider == "gemini"
                               else ANTHROPIC_MODEL)
        self.calls: list[dict] = []
        self._client = None

    # ---- cache -----------------------------------------------------------
    def _path(self, key: str) -> str:
        return os.path.join(self.cache_dir, f"{key}.json")

    def _read_cache(self, key: str):
        p = self._path(key)
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)

    def _write_cache(self, key: str, record: dict) -> None:
        os.makedirs(self.cache_dir, exist_ok=True)
        with open(self._path(key), "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2, sort_keys=True)

    def cache_key(self, system: str, messages: list, schema=None, **kw) -> str:
        blob = json.dumps(
            {"provider": self.provider, "model": self.model, "system": system,
             "messages": messages, "schema": schema, **kw},
            sort_keys=True, separators=(",", ":"), default=str,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    # ---- entry point -----------------------------------------------------
    def complete(self, system: str, messages: list, *, schema=None,
                 max_tokens: int = 2000, effort: str = "medium",
                 template=None) -> Completion:
        """Call the model, or replay a cached answer.

        schema constrains the reply to a JSON schema. template is used only
        in offline mode.
        """
        key = self.cache_key(system, messages, schema, max_tokens=max_tokens,
                             effort=effort)
        self.calls.append({"key": key, "system": system[:80],
                           "n_messages": len(messages), "schema": bool(schema)})

        hit = self._read_cache(key)
        if hit is not None:
            return Completion(hit["text"], "cache", key, hit.get("usage", {}),
                              model=hit.get("model"))

        if self.mode == "replay":
            raise CacheMiss(
                f"no cached reply for {key}. Run once with mode='live' to record it."
            )

        if self.mode == "offline":
            if template is None:
                raise CacheMiss(f"no cached reply for {key} and no template supplied")
            return Completion(template(), "template", key)   # model stays None

        text, usage = self._call(system, messages, schema, max_tokens, effort)
        self._write_cache(key, {"text": text, "usage": usage, "model": self.model,
                                "provider": self.provider, "system": system,
                                "messages": messages})
        return Completion(text, self.provider, key, usage, model=self.model)

    def _call(self, system, messages, schema, max_tokens, effort):
        """Retry transient failures. Gemini returns 503 under load often enough
        that a single attempt is not a fair test of whether the call works."""
        last = None
        for attempt in range(MAX_RETRIES):
            try:
                if self.provider == "gemini":
                    return self._call_gemini(system, messages, schema, max_tokens)
                return self._call_anthropic(system, messages, schema, max_tokens, effort)
            except NoCredentials:
                raise
            except Exception as e:
                msg = str(e)
                transient = any(c in msg for c in ("503", "UNAVAILABLE", "429",
                                                   "overloaded", "RESOURCE_EXHAUSTED"))
                last = e
                if not transient or attempt == MAX_RETRIES - 1:
                    raise
                time.sleep(RETRY_SLEEP * (attempt + 1))
        raise last

    # ---- providers -------------------------------------------------------
    def _call_anthropic(self, system, messages, schema, max_tokens, effort):
        if self._client is None:
            try:
                import anthropic
            except ImportError as e:
                raise NoCredentials("anthropic SDK not installed") from e
            try:
                self._client = anthropic.Anthropic()
            except Exception as e:
                raise NoCredentials(f"could not construct client: {e}") from e

        kwargs = dict(model=self.model, max_tokens=max_tokens, system=system,
                      messages=messages, thinking={"type": "adaptive"},
                      output_config={"effort": effort})
        if schema is not None:
            kwargs["output_config"] = {
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            }
        resp = self._client.messages.create(**kwargs)
        if getattr(resp, "stop_reason", None) == "refusal":
            raise RuntimeError(f"model declined: {getattr(resp, 'stop_details', None)}")
        text = "".join(b.text for b in resp.content if b.type == "text")
        return text, {"input_tokens": resp.usage.input_tokens,
                      "output_tokens": resp.usage.output_tokens}

    def _call_gemini(self, system, messages, schema, max_tokens):
        if self._client is None:
            key = gemini_key()
            if not key:
                raise NoCredentials("no GEMINI_API_KEY in environment or .env")
            try:
                import truststore
                truststore.inject_into_ssl()   # this machine intercepts TLS
            except ImportError:
                pass
            try:
                from google import genai
            except ImportError as e:
                raise NoCredentials("google-genai not installed") from e
            self._client = genai.Client(api_key=key)

        # Gemini calls the assistant role "model".
        contents = [{"role": "model" if m["role"] == "assistant" else "user",
                     "parts": [{"text": m["content"]}]} for m in messages]
        cfg = {"system_instruction": system,
               "max_output_tokens": max_tokens + THINKING_HEADROOM}
        if schema is not None:
            cfg["response_mime_type"] = "application/json"
            cfg["response_schema"] = _gemini_schema(schema)

        resp = self._client.models.generate_content(
            model=self.model, contents=contents, config=cfg)
        text = resp.text or ""
        if not text.strip():
            raise RuntimeError(f"empty reply (finish={getattr(resp, 'candidates', None)})")
        u = resp.usage_metadata
        return text, {"input_tokens": u.prompt_token_count,
                      "output_tokens": u.candidates_token_count,
                      "thinking_tokens": getattr(u, "thoughts_token_count", None)}

    # ---- reporting -------------------------------------------------------
    def summary(self) -> dict:
        return {"mode": self.mode, "provider": self.provider, "model": self.model,
                "n_calls": len(self.calls)}
