"""The agent's only route to the model and the policy.

Thin wrappers over Phase 1 calls, each one logged. No function takes text as
an argument, so generated prose has no way to reach policy.decide.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from core.feature_store import Evidence, FeatureStore
from core.model import Adjudicator
from core.policy import Action, Decision, PolicyConfig, Verification, decide


@dataclass
class ToolCall:
    name: str
    args: dict
    result: dict
    ms: float


@dataclass
class Toolbox:
    store: FeatureStore
    model: Adjudicator
    cfg: PolicyConfig
    log: list = field(default_factory=list)

    def _record(self, name, args, result, t0):
        self.log.append(ToolCall(name, args, result, (time.perf_counter() - t0) * 1000))
        return result

    # ---- evidence --------------------------------------------------------
    def get_case(self, payment_id: str) -> Evidence:
        t0 = time.perf_counter()
        ev = next((e for e in self.store.cases if e.payment_id == payment_id), None)
        if ev is None:
            raise KeyError(payment_id)
        self._record("get_case", {"payment_id": payment_id},
                     {"merchant": ev.merchant, "amount_inr": ev.amount_inr,
                      "block_reason": ev.meta["block_reason"]}, t0)
        return ev

    def get_network_file(self, ev: Evidence) -> dict:
        t0 = time.perf_counter()
        f = dict(ev.network)
        return self._record("get_network_file", {"payment_id": ev.payment_id}, f, t0)

    def get_merchant_file(self, ev: Evidence) -> dict:
        t0 = time.perf_counter()
        f = {k: v for k, v in ev.local.items() if k.startswith("f_")}
        f["risk_score"] = ev.local["risk_score"]
        f["threshold"] = ev.meta["threshold"]
        return self._record("get_merchant_file", {"payment_id": ev.payment_id}, f, t0)

    # ---- scoring and decision --------------------------------------------
    def adjudicate(self, ev: Evidence) -> float:
        t0 = time.perf_counter()
        p = self.model.predict_one(ev)
        self._record("adjudicate", {"payment_id": ev.payment_id},
                     {"p_bad": round(p, 6)}, t0)
        return p

    def policy_decide(self, p_bad: float, ev: Evidence,
                      verification: Verification | None = None) -> Decision:
        t0 = time.perf_counter()
        d = decide(p_bad, ev, self.cfg, verification)
        self._record("policy_decide",
                     {"payment_id": ev.payment_id, "p_bad": round(p_bad, 6),
                      "cap": self.cfg.cap, "verified": bool(
                          verification and verification.clears_gate)},
                     {"action": d.action.value, "ev_release_inr": round(d.ev_release_inr, 2),
                      "reasons": list(d.reasons)}, t0)
        return d

    # ---- effects ---------------------------------------------------------
    def record_verdict(self, ev: Evidence, d: Decision, text: str, source: str) -> dict:
        t0 = time.perf_counter()
        return self._record("record_verdict", {"payment_id": ev.payment_id},
                            {"action": d.action.value, "chars": len(text),
                             "source": source}, t0)

    def escalate(self, ev: Evidence, d: Decision, brief: str) -> dict:
        t0 = time.perf_counter()
        return self._record("escalate", {"payment_id": ev.payment_id},
                            {"amount_inr": ev.amount_inr, "reasons": list(d.reasons),
                             "brief_chars": len(brief)}, t0)

    # ---- reporting -------------------------------------------------------
    def calls_for(self, payment_id: str) -> list:
        return [c for c in self.log if c.args.get("payment_id") == payment_id]

    def trace(self, payment_id: str) -> list:
        return [{"tool": c.name, "result": c.result, "ms": round(c.ms, 3)}
                for c in self.calls_for(payment_id)]


# Kept as data so a test can check the Toolbox exposes all of them.
TOOL_NAMES = ("get_case", "get_network_file", "get_merchant_file", "adjudicate",
              "policy_decide", "record_verdict", "escalate")
