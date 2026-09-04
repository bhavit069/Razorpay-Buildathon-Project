"""The case loop.

    for case in docket:
        adjudicate -> policy_decide
        if STEP_UP on a thin file: verify, then decide again
        write the verdict
        append to the ledger

Adds no decision logic. Every action comes from core.policy.decide, reached
only through tools.py.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from core.backtest import Ledger as CoreLedger, LedgerRecord
from core.feature_store import Evidence, FeatureStore
from core.model import Adjudicator
from core.policy import Action, Decision, PolicyConfig, Verification

from . import stepup as stepup_mod
from . import verdict as verdict_mod
from .ledger import Ledger
from .llm import LLMClient
from .tools import Toolbox


@dataclass
class CaseResult:
    payment_id: str
    action: str
    p_bad: float
    amount_inr: float
    verdict: str
    verdict_source: str
    verdict_provenance: str       # what to print on screen next to the text
    verdict_from_model: bool
    citations_ok: bool
    brief: str = ""
    stepped_up: bool = False
    action_before_stepup: str | None = None
    p_bad_before_stepup: float | None = None
    prepaid_swap: bool = False
    transcript: list = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    tool_trace: list = field(default_factory=list)
    ms: float = 0.0


class Orchestrator:
    def __init__(self, store: FeatureStore, model: Adjudicator,
                 cfg: PolicyConfig | None = None, llm: LLMClient | None = None,
                 ledger_path: str | None = None, personas: dict | None = None):
        self.cfg = cfg or PolicyConfig()
        self.tools = Toolbox(store, model, self.cfg)
        self.llm = llm or LLMClient(mode="offline")
        self.ledger = Ledger(ledger_path)
        # payment_id -> (persona, outcome), for the simulated customer only.
        self.personas = personas or {}

    def handle(self, ev: Evidence) -> CaseResult:
        t0 = time.perf_counter()

        p = self.tools.adjudicate(ev)
        d: Decision = self.tools.policy_decide(p, ev)

        stepped, before_action, before_p, prepaid = False, None, None, False
        transcript, facts = [], {}

        # Only thin-file step-ups get an exchange. Ambiguous-branch cases
        # already have a full record, so verification cannot move them.
        if d.action is Action.STEP_UP and not d.evidence_sufficient:
            before_action, before_p = d.action.value, p
            persona, outcome = self.personas.get(ev.payment_id,
                                                 ("legit_stable", "clean"))
            su = stepup_mod.run(ev, self.llm, persona, outcome)
            transcript, facts, prepaid = su.transcript, vars(su.facts), su.prepaid
            stepped = True

            # Same policy call, with the verification. p_bad is not recomputed:
            # the record did not change. See core.policy.Verification.
            if su.verification.clears_gate:
                d = self.tools.policy_decide(p, ev, su.verification)

        v = verdict_mod.write(ev, d, self.llm)
        self.tools.record_verdict(ev, d, v.text, v.source)

        # Escalations get their own note, written for the reviewer.
        brief = ""
        if d.action is Action.ESCALATE:
            b = verdict_mod.write_brief(ev, d, self.llm)
            brief = b.text
            self.tools.escalate(ev, d, brief)

        ms = (time.perf_counter() - t0) * 1000
        result = CaseResult(
            payment_id=ev.payment_id, action=d.action.value, p_bad=float(p),
            amount_inr=ev.amount_inr, verdict=v.text, verdict_source=v.source,
            verdict_provenance=v.provenance, verdict_from_model=v.from_model,
            citations_ok=v.citations_ok, brief=brief, stepped_up=stepped,
            action_before_stepup=before_action, p_bad_before_stepup=before_p,
            prepaid_swap=prepaid, transcript=transcript, facts=facts,
            tool_trace=self.tools.trace(ev.payment_id), ms=ms,
        )
        self.ledger.append({
            "payment_id": result.payment_id,
            "merchant": ev.merchant,
            "amount_inr": round(ev.amount_inr, 2),
            "action": result.action,
            "p_bad": round(result.p_bad, 6),
            "cap": self.cfg.cap,
            "stepped_up": stepped,
            "prepaid_swap": prepaid,
            "verdict": result.verdict,
            "verdict_source": result.verdict_source,
            "verdict_provenance": result.verdict_provenance,
            "citations_ok": result.citations_ok,
            "brief": brief,
            "tools": [t["tool"] for t in result.tool_trace],
        })
        return result

    def run(self, cases, limit: int | None = None) -> list:
        out = []
        for i, ev in enumerate(cases):
            if limit is not None and i >= limit:
                break
            out.append(self.handle(ev))
        return out

    def as_core_ledger(self, results: list) -> CoreLedger:
        """Re-express an agent run as a Phase 1 ledger, so core.metrics.grade
        prices it with the same code that prices a backtest."""
        recs = []
        t0 = min(self.tools.store.cases[0].created_at,
                 *(self._ev(r).created_at for r in results)) if results else 0
        for i, r in enumerate(results):
            ev = self._ev(r)
            recs.append(LedgerRecord(
                seq=i, payment_id=r.payment_id, created_at=ev.created_at,
                day=(ev.created_at - t0) // 86_400, merchant=ev.merchant,
                amount_inr=r.amount_inr, block_reason=ev.meta["block_reason"],
                p_bad=r.p_bad,
                network_orders_prior=ev.network["network_orders_prior"],
                network_tenure_days=ev.network["network_tenure_days"],
                action=r.action, ev_release_inr=0.0,
                evidence_sufficient=True, reasons=(),
            ))
        return CoreLedger(recs, self.cfg, "holdout", tuple(self.tools.model.blocks))

    def _ev(self, r) -> Evidence:
        return next(e for e in self.tools.store.cases if e.payment_id == r.payment_id)

    def summary(self, results: list) -> dict:
        n = len(results) or 1
        return {
            "cases": len(results),
            "actions": {a.value: sum(r.action == a.value for r in results)
                        for a in Action},
            "stepped_up": sum(r.stepped_up for r in results),
            "prepaid_swaps": sum(r.prepaid_swap for r in results),
            "verdicts_audit_clean": sum(r.citations_ok for r in results),
            # "claude" was never a source string, so that bucket always read 0
            # and the summary silently under-reported model output.
            "verdict_sources": {s: sum(r.verdict_source == s for r in results)
                                for s in ("anthropic", "gemini", "cache", "template")},
            "verdicts_from_model": sum(r.verdict_from_model for r in results),
            "latency_ms": self._latency(results),
            "ledger_entries": len(self.ledger),
            "llm": self.llm.summary(),
        }

    @staticmethod
    def _latency(results: list) -> dict:
        if not results:
            return {}
        ms = sorted(r.ms for r in results)
        at = lambda q: round(ms[min(int(q * len(ms)), len(ms) - 1)], 2)
        return {"p50": at(0.50), "p95": at(0.95), "max": round(ms[-1], 2)}
