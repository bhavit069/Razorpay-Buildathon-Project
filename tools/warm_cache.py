#!/usr/bin/env python3
"""Record live model replies for the demo cases only.

A full live pass is not affordable on this key: the quota is tight and every
call carries thinking overhead. The demo does not need one. It needs the
showcase cases to have real model output, cached, so `--mode replay` serves
them off-network.

    python warm_cache.py [--limit N] [--sleep S]

Everything it records lands in agent/cache/ and is replayed byte-identically
afterwards. Cases it cannot reach stay on the template path and stay labelled
as such.
"""
import os as _os, sys as _sys
_R = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _R not in _sys.path:
    _sys.path.insert(0, _R)

from __future__ import annotations

import argparse
import time

from agent.llm import LLMClient
from agent.orchestrator import Orchestrator
from core.feature_store import FeatureStore
from core.model import Adjudicator
from core.policy import PolicyConfig
from core.showcase import pick as pick_showcase
from core.truth import TruthVault


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data300k")
    ap.add_argument("--cap", type=float, default=0.20)
    ap.add_argument("--limit", type=int, default=8, help="how many cases to warm")
    ap.add_argument("--sleep", type=float, default=6.0, help="pause between cases")
    a = ap.parse_args()

    store, vault = FeatureStore.load(a.data), TruthVault(a.data)
    model = Adjudicator().fit(store, vault)
    personas = {t.payment_id: (t.persona, t.true_outcome)
                for t in vault.grade(store.payment_ids(store.split("holdout")))}

    picked = pick_showcase(store, vault, model)
    targets = [e for e, _, _ in picked.values()][:a.limit]
    print(f"warming {len(targets)} demo cases with {LLMClient('live').summary()}")

    orc = Orchestrator(store, model, PolicyConfig(cap=a.cap),
                       LLMClient("live"), None, personas)

    ok = fail = 0
    for i, ev in enumerate(targets, 1):
        try:
            r = orc.handle(ev)
            mark = "model" if r.verdict_source != "template" else "template"
            print(f"  [{i}/{len(targets)}] {ev.payment_id} {r.action:<9} {mark}")
            print(f"      {r.verdict[:150]}")
            ok += r.verdict_source != "template"
        except Exception as e:
            fail += 1
            print(f"  [{i}/{len(targets)}] {ev.payment_id} FAILED {type(e).__name__}: "
                  f"{str(e)[:90]}")
        time.sleep(a.sleep)

    print(f"\n{ok} cases now have live model verdicts, {fail} failed.")
    print("Re-run demo.py --mode replay to serve them from cache.")


if __name__ == "__main__":
    main()
