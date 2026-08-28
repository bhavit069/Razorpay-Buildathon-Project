#!/usr/bin/env python3
"""Run the agent over the holdout docket.

    python demo.py [--limit N] [--cap C] [--mode offline|replay|live]

Modes:
    offline   deterministic templates where no cached reply exists (default)
    replay    cache only; fails loudly on a miss. Use for a demo dry run.
    live      calls Claude and records each reply into agent/cache/
"""
from __future__ import annotations

import argparse
import json

from agent.llm import LLMClient
from agent.orchestrator import Orchestrator
from core.backtest import run as backtest_run
from core.feature_store import FeatureStore
from core.metrics import grade
from core.model import Adjudicator
from core.policy import PolicyConfig
from core.truth import TruthVault


def cr(x):
    a = abs(x)
    sign = "-" if x < 0 else ""
    if a >= 1e7:
        return f"{sign}Rs {a/1e7:,.2f} cr"
    if a >= 1e5:
        return f"{sign}Rs {a/1e5:,.2f} L"
    return f"{sign}Rs {a:,.0f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data300k")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--cap", type=float, default=0.02)
    ap.add_argument("--mode", default="offline", choices=("offline", "replay", "live"))
    ap.add_argument("--ledger", default="artifacts/ledger.jsonl")
    ap.add_argument("--show", type=int, default=2, help="verdicts to print per action")
    a = ap.parse_args()

    store, vault = FeatureStore.load(a.data), TruthVault(a.data)
    model = Adjudicator().fit(store, vault)

    # Ground truth reaches the agent plane at exactly one point: seeding the
    # simulated customer in the step-up exchange. Nothing downstream sees it.
    personas = {t.payment_id: (t.persona, t.true_outcome)
                for t in vault.grade(store.payment_ids(store.split("holdout")))}

    orc = Orchestrator(store, model, PolicyConfig(cap=a.cap),
                       LLMClient(a.mode), a.ledger, personas)
    results = orc.run(store.split("holdout"), limit=a.limit)

    print(json.dumps(orc.summary(results), indent=2))
    orc.ledger.verify()
    print(f"\nledger verified: {len(orc.ledger)} entries, head {orc.ledger.head[:16]}")

    # Priced with the Phase 1 grader, unchanged, so these sit on the same
    # footing as METRICS.md rather than being a separate accounting.
    o = grade(orc.as_core_ledger(results), vault, None)
    print(f"\n=== priced (core.metrics.grade, cap={a.cap}) ===")
    print(f"  released           {o.n_released} of {o.n_cases}")
    print(f"  precision          {o.precision:.1%}")
    print(f"  recall recoverable {o.recall_recoverable:.1%}")
    print(f"  recovered          {cr(o.recovered_inr)}")
    print(f"  fraud admitted     {cr(o.fraud_admitted_inr)}")
    print(f"  net contribution   {cr(o.net_contribution_inr)}")
    print(f"  abstained          {o.abstention_rate:.1%}")

    # The architectural claim, rechecked on every run rather than asserted.
    bt = {r.payment_id: r for r in
          backtest_run(store, model, PolicyConfig(cap=a.cap), "holdout").records}
    drift = [r for r in results if r.p_bad != bt[r.payment_id].p_bad]
    diverged = [r for r in results if r.action != bt[r.payment_id].action]
    print("\n=== agent vs backtest ===")
    print(f"  p_bad drift        {len(drift)} of {len(results)}")
    print(f"  action divergence  {len(diverged)} of {len(results)}"
          f"   all from step-ups: {all(r.stepped_up for r in diverged)}")
    for r in diverged[:3]:
        print(f"    {r.payment_id}  {bt[r.payment_id].action} -> {r.action}"
              f"  p_bad unchanged at {r.p_bad:.3f}")

    truth = {v.payment_id: v for v in vault.grade([r.payment_id for r in results])}
    for tag in ("OVERTURN", "UPHOLD", "STEP_UP", "ESCALATE"):
        group = [r for r in results if r.action == tag]
        if not group:
            continue
        if tag in ("OVERTURN", "UPHOLD"):
            right = sum((truth[r.payment_id].true_outcome == "clean")
                        == (tag == "OVERTURN") for r in group)
            print(f"\n=== {tag}  {len(group)} cases, {right/len(group):.1%} correct ===")
        else:
            print(f"\n=== {tag}  {len(group)} cases ===")
        for r in group[:a.show]:
            print(f"\n  {r.payment_id}  Rs {r.amount_inr:,.0f}  p_bad {r.p_bad:.3f}"
                  f"  [{r.verdict_source}]  truth={truth[r.payment_id].true_outcome}")
            print(f"  {r.verdict}")

    esc = [r for r in results if r.brief]
    if esc:
        print(f"\n=== escalation brief: {esc[0].payment_id} ===")
        for line in esc[0].brief.splitlines():
            print(f"  {line}")

    swung = [r for r in results if r.stepped_up and r.action_before_stepup != r.action]
    if swung:
        r = swung[0]
        print(f"\n=== step-up that resolved a case: {r.payment_id} ===")
        print(f"  {r.action_before_stepup} -> {r.action}, p_bad unchanged at {r.p_bad:.3f}")
        for t in r.transcript:
            print(f"    {t['role']:>8}: {t['text']}")
        print(f"  facts: {r.facts}")


if __name__ == "__main__":
    main()
