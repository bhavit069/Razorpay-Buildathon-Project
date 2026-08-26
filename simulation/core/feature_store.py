"""Evidence assembly for one appealed case.

The generator froze every feature point-in-time at the moment of the block, so
this validates rather than recomputes. validate() checks columns are present,
finite and within declared ranges; test_leakage.py checks the point-in-time
property separately.

Must not import core.truth.
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field

import numpy as np

# Merchant-visible: 12 scorecard signals, the score they produced, the amount.
LOCAL_FEATURES = [
    "f_device_is_new",
    "f_device_account_fanout",
    "f_address_mismatch",
    "f_orders_last_24h",
    "f_amount_z",
    "f_pincode_rto_propensity",
    "f_is_night",
    "f_thin_file_flag",
    "f_disposable_email",
    "f_international",
    "f_merchant_prior_rto",
    "f_is_cod",
    "risk_score",
    "amount",
]

# Platform-only. ARCHITECTURE.md lists seven; network_instrument_merchants is
# added here (one instrument across N merchants, the Case-2 signal).
NETWORK_FEATURES = [
    "network_orders_prior",
    "network_merchants_prior",
    "network_tenure_days",
    "network_clean_rate",
    "network_disputes_prior",
    "network_rto_prior",
    "network_device_fanout",
    "network_instrument_merchants",
]

_NUMERIC_META = {"created_at", "amount", "threshold", "risk_score", "is_cod"}

META_FIELDS = [
    "decision_id", "payment_id", "order_id", "merchant_id", "merchant_name",
    "customer_id", "created_at", "block_reason", "amount", "method", "is_cod",
    "threshold", "risk_score", "split",
]

BLOCKS = {"local": LOCAL_FEATURES, "network": NETWORK_FEATURES}

# A violation means the generator changed.
_RANGES = {
    "risk_score": (0.0, 1.0),
    "network_clean_rate": (0.0, 1.0),
    "network_tenure_days": (0.0, 20_000.0),
    "amount": (1.0, 10_000_000_000.0),
    "f_pincode_rto_propensity": (0.0, 5.0),
}


@dataclass(frozen=True)
class Evidence:
    """One case, in three blocks. No labels."""
    payment_id: str
    local: dict = field(repr=False)
    network: dict = field(repr=False)
    meta: dict = field(repr=False)

    @property
    def amount_inr(self) -> float:
        return self.meta["amount"] / 100.0

    @property
    def created_at(self) -> int:
        return int(self.meta["created_at"])

    @property
    def merchant(self) -> str:
        return self.meta["merchant_name"]

    @property
    def split(self) -> str:
        return self.meta["split"]

    def as_vector(self, blocks=("local", "network")) -> np.ndarray:
        vals = []
        for b in blocks:
            src = self.local if b == "local" else self.network
            vals.extend(float(src[c]) for c in BLOCKS[b])
        return np.array(vals, dtype=np.float64)

    def merged(self, deltas: dict) -> "Evidence":
        """Copy with network evidence updated. Used by the step-up path, which
        may only add evidence for the same policy to re-price."""
        unknown = set(deltas) - set(NETWORK_FEATURES)
        if unknown:
            raise KeyError(f"step-up may only refine network evidence, got {unknown}")
        return Evidence(self.payment_id, dict(self.local),
                        {**self.network, **deltas}, dict(self.meta))


class LeakageError(Exception):
    pass


class FeatureStore:
    """The appeal queue. Knows nothing about outcomes."""

    def __init__(self, cases: list[Evidence], source: str):
        self.cases = cases
        self.source = source

    # ---- construction ----------------------------------------------------
    @classmethod
    def load(cls, data_dir: str) -> "FeatureStore":
        path = os.path.join(data_dir, "appeal_queue.csv")
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        if not rows:
            raise ValueError(f"{path} is empty")

        forbidden = {"persona", "true_outcome", "is_false_positive",
                     "is_true_positive", "was_blocked"}
        present = forbidden & set(rows[0])
        if present:
            raise LeakageError(f"appeal queue carries answer-key columns: {present}")

        cases = []
        for r in rows:
            if r["action"] != "block":
                continue
            cases.append(Evidence(
                payment_id=r["payment_id"],
                local={c: _num(r[c]) for c in LOCAL_FEATURES},
                network={c: _num(r[c]) for c in NETWORK_FEATURES},
                meta={c: (_num(r[c]) if c in _NUMERIC_META else r[c])
                      for c in META_FIELDS},
            ))
        cases.sort(key=lambda e: (e.created_at, e.payment_id))
        store = cls(cases, source=path)
        store.validate()
        return store

    # ---- validation ------------------------------------------------------
    def validate(self) -> None:
        for e in self.cases:
            for name, val in (*e.local.items(), *e.network.items()):
                if not np.isfinite(val):
                    raise ValueError(f"{e.payment_id}: {name} is not finite ({val})")
                lo, hi = _RANGES.get(name, (None, None))
                if lo is not None and not (lo <= val <= hi):
                    raise ValueError(
                        f"{e.payment_id}: {name}={val} outside declared [{lo}, {hi}]"
                    )
        ts = [e.created_at for e in self.cases]
        if ts != sorted(ts):
            raise ValueError("cases are not in chronological order")

    # ---- access ----------------------------------------------------------
    def split(self, name: str) -> list[Evidence]:
        return [e for e in self.cases if e.split == name]

    def payment_ids(self, cases=None) -> list[str]:
        return [e.payment_id for e in (self.cases if cases is None else cases)]

    def as_matrix(self, cases=None, blocks=("local", "network")) -> np.ndarray:
        cases = self.cases if cases is None else cases
        for b in blocks:
            if b not in BLOCKS:
                raise KeyError(f"unknown evidence block {b!r}; have {list(BLOCKS)}")
        return np.vstack([c.as_vector(blocks) for c in cases])

    @staticmethod
    def feature_names(blocks=("local", "network")) -> list[str]:
        return [c for b in blocks for c in BLOCKS[b]]

    def __len__(self) -> int:
        return len(self.cases)

    def __repr__(self) -> str:
        n = {s: len(self.split(s)) for s in ("train", "holdout")}
        return f"<FeatureStore {len(self)} cases train={n['train']} holdout={n['holdout']}>"


def _num(v: str) -> float:
    if v in ("True", "true"):
        return 1.0
    if v in ("False", "false"):
        return 0.0
    return float(v)
