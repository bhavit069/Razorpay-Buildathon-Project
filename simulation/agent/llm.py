"""Claude access with an on-disk replay cache.

Calls are keyed by a hash of their inputs and stored in `agent/cache/`, so a
repeat call replays from disk. Modes:

    live      call the API on a miss, store the result
    replay    cache only, a miss raises
    offline   cache only, falling back to a template

Offline output is tagged source="template" so it cannot be mistaken for model
output.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

MODEL = "claude-opus-5"
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")


class CacheMiss(Exception):
    pass


class NoCredentials(Exception):
    pass


@dataclass
class Completion:
    text: str
    source: str            # "claude" | "cache" | "template"
    key: str
    usage: dict = field(default_factory=dict)

    @property
    def from_model(self) -> bool:
        """True if a model produced this text, now or earlier."""
        return self.source in ("claude", "cache")


def cache_key(system: str, messages: list, schema=None, **kw) -> str:
    blob = json.dumps(
        {"model": MODEL, "system": system, "messages": messages,
         "schema": schema, **kw},
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


class LLMClient:
    def __init__(self, mode: str = "offline", cache_dir: str = CACHE_DIR,
                 model: str = MODEL):
        if mode not in ("live", "replay", "offline"):
            raise ValueError(f"unknown mode {mode!r}")
        self.mode = mode
        self.cache_dir = cache_dir
        self.model = model
        self.calls: list[dict] = []          # everything asked of the model
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

    # ---- entry point -----------------------------------------------------
    def complete(self, system: str, messages: list, *, schema=None,
                 max_tokens: int = 2000, effort: str = "medium",
                 template=None) -> Completion:
        """Call the model, or replay a cached answer.

        schema constrains the reply to a JSON schema. template is used only
        in offline mode.
        """
        key = cache_key(system, messages, schema, max_tokens=max_tokens,
                        effort=effort)
        self.calls.append({"key": key, "system": system[:80], "n_messages": len(messages),
                           "schema": bool(schema)})

        hit = self._read_cache(key)
        if hit is not None:
            return Completion(hit["text"], "cache", key, hit.get("usage", {}))

        if self.mode == "replay":
            raise CacheMiss(
                f"no cached reply for {key}. Run once with mode='live' to record it."
            )

        if self.mode == "offline":
            if template is None:
                raise CacheMiss(f"no cached reply for {key} and no template supplied")
            return Completion(template(), "template", key)

        text, usage = self._call_api(system, messages, schema, max_tokens, effort)
        self._write_cache(key, {"text": text, "usage": usage, "model": self.model,
                                "system": system, "messages": messages})
        return Completion(text, "claude", key, usage)

    def _call_api(self, system, messages, schema, max_tokens, effort):
        if self._client is None:
            try:
                import anthropic
            except ImportError as e:
                raise NoCredentials("anthropic SDK not installed") from e
            try:
                self._client = anthropic.Anthropic()
            except Exception as e:
                raise NoCredentials(f"could not construct client: {e}") from e

        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
        )
        if schema is not None:
            kwargs["output_config"] = {
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            }

        resp = self._client.messages.create(**kwargs)
        if getattr(resp, "stop_reason", None) == "refusal":
            raise RuntimeError(f"model declined: {getattr(resp, 'stop_details', None)}")
        text = "".join(b.text for b in resp.content if b.type == "text")
        usage = {"input_tokens": resp.usage.input_tokens,
                 "output_tokens": resp.usage.output_tokens}
        return text, usage

    # ---- reporting -------------------------------------------------------
    def summary(self) -> dict:
        return {"mode": self.mode, "model": self.model, "n_calls": len(self.calls)}
