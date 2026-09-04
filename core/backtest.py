"""Chronological replay over the blocked pile.

A replay rather than a batch predict, so the daily and cumulative series fall
out of the ordering and any ordering bug surfaces.

No truth in this file. The ledger records decisions; metrics.py grades them
afterwards. Step-up outcomes are left unresolved here and resolved
parametrically at grading time, since the dataset cannot say whether a
customer would pass verification.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, replace

import numpy as np

from .feature_store import FeatureStore
from .policy import Action, Decision, PolicyConfig, decide


@dataclass(frozen=True)
class LedgerRecord:
    seq: int
    payment_id: str
    created_at: int
    day: int                 # days since the first case in the run
    merchant: str
    amount_inr: float
    block_reason: str
    p_bad: float
    # the two quantities the insufficiency gate reads, carried so the ledger is
    # self-describing and redecide() needs no side table
    network_orders_prior: float
    network_tenure_days: float
    action: str
    ev_release_inr: float
    evidence_sufficient: bool
    reasons: tuple

    def to_json(self) -> str:
        d = asdict(self)
        d["reasons"] = list(d["reasons"])
        # rounded so the determinism check compares decisions, not float noise
        d["p_bad"] = round(d["p_bad"], 9)
        d["ev_release_inr"] = round(d["ev_release_inr"], 4)
        d["amount_inr"] = round(d["amount_inr"], 2)
        return json.dumps(d, sort_keys=True, separators=(",", ":"))


@dataclass
class Ledger:
    records: list
    config: PolicyConfig
    split: str
    blocks: tuple

    def __len__(self) -> int:
        return len(self.records)

    def to_jsonl(self) -> str:
        return "\n".join(r.to_json() for r in self.records) + "\n"

    def payment_ids(self) -> list:
        return [r.payment_id for r in self.records]

    def actions(self) -> np.ndarray:
        return np.array([r.action for r in self.records])

    def released(self) -> np.ndarray:
        return self.actions() == Action.OVERTURN.value

    def amounts(self) -> np.ndarray:
        return np.array([r.amount_inr for r in self.records])

    def p_bad(self) -> np.ndarray:
        return np.array([r.p_bad for r in self.records])

    def counts(self) -> dict:
        out = {a.value: 0 for a in Action}
        for r in self.records:
            out[r.action] += 1
        return out

    def redecide(self, cfg: PolicyConfig) -> "Ledger":
        """Re-run the policy at a new operating point. p_bad is unchanged: the
        model does not move when risk appetite does. Sweeping `cap` through
        this method produces the frontier."""
        out = []
        for r in self.records:
            ev = _shim(r)
            d = decide(r.p_bad, ev, cfg)
            out.append(replace(r, action=d.action.value,
                               ev_release_inr=d.ev_release_inr,
                               evidence_sufficient=d.evidence_sufficient,
                               reasons=d.reasons))
        return Ledger(out, cfg, self.split, self.blocks)


class _shim:
    """Minimal Evidence-alike so redecide() reuses policy.decide() unchanged."""
    __slots__ = ("payment_id", "amount_inr", "network")

    def __init__(self, r: LedgerRecord):
        self.payment_id = r.payment_id
        self.amount_inr = r.amount_inr
        self.network = {"network_orders_prior": r.network_orders_prior,
                        "network_tenure_days": r.network_tenure_days}


def run(store: FeatureStore, model, cfg: PolicyConfig = PolicyConfig(),
        split: str = "holdout") -> Ledger:
    cases = store.split(split)
    if not cases:
        raise ValueError(f"no cases in split {split!r}")

    # Scored in one call, decided one at a time in date order. The model is
    # stateless across cases so batching the scoring changes nothing.
    probs = model.predict(store, cases)
    t0 = cases[0].created_at

    records = []
    for seq, (ev, p) in enumerate(zip(cases, probs)):
        d: Decision = decide(float(p), ev, cfg)
        records.append(LedgerRecord(
            seq=seq,
            payment_id=ev.payment_id,
            created_at=ev.created_at,
            day=(ev.created_at - t0) // 86_400,
            merchant=ev.merchant,
            amount_inr=ev.amount_inr,
            block_reason=ev.meta["block_reason"],
            p_bad=float(p),
            network_orders_prior=ev.network["network_orders_prior"],
            network_tenure_days=ev.network["network_tenure_days"],
            action=d.action.value,
            ev_release_inr=d.ev_release_inr,
            evidence_sufficient=d.evidence_sufficient,
            reasons=d.reasons,
        ))
    return Ledger(records, cfg, split, tuple(model.blocks))
