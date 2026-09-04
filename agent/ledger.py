"""Append-only, hash-chained case log.

Each record stores sha256(prev_hash + record). Editing an earlier record
breaks every hash after it, so tampering is detectable.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass

GENESIS = "0" * 64


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def link(prev_hash: str, payload: dict) -> str:
    return hashlib.sha256((prev_hash + _canonical(payload)).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Entry:
    seq: int
    prev_hash: str
    hash: str
    payload: dict

    def to_json(self) -> str:
        return json.dumps(
            {"seq": self.seq, "prev_hash": self.prev_hash, "hash": self.hash,
             "payload": self.payload},
            sort_keys=True, separators=(",", ":"), default=str,
        )

    @classmethod
    def from_json(cls, line: str) -> "Entry":
        d = json.loads(line)
        return cls(d["seq"], d["prev_hash"], d["hash"], d["payload"])


class BrokenChain(Exception):
    pass


class Ledger:
    def __init__(self, path: str | None = None):
        self.path = path
        self.entries: list[Entry] = []
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                self.entries = [Entry.from_json(l) for l in fh if l.strip()]

    @property
    def head(self) -> str:
        return self.entries[-1].hash if self.entries else GENESIS

    def append(self, payload: dict) -> Entry:
        entry = Entry(len(self.entries), self.head, link(self.head, payload), payload)
        self.entries.append(entry)
        if self.path:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(entry.to_json() + "\n")
        return entry

    def verify(self) -> None:
        """Raise BrokenChain naming the first bad entry."""
        prev = GENESIS
        for e in self.entries:
            if e.prev_hash != prev:
                raise BrokenChain(f"entry {e.seq}: prev_hash does not match entry {e.seq-1}")
            if link(prev, e.payload) != e.hash:
                raise BrokenChain(f"entry {e.seq}: payload does not hash to its recorded hash")
            prev = e.hash

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries)

    def find(self, payment_id: str) -> Entry | None:
        for e in self.entries:
            if e.payload.get("payment_id") == payment_id:
                return e
        return None
