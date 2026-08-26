#!/usr/bin/env python3
"""
Regenerate the world under several seeds and re-run the whole pipeline on each.

The bootstrap intervals in METRICS.md measure sampling error inside one
generated dataset. They say nothing about how much the dataset itself moves.
This does. Run it before quoting any rupee figure.

    python seed_check.py [n_seeds]
"""
import json
import os
import subprocess
import sys
import tempfile

import numpy as np
from sklearn.metrics import roc_auc_score

from core.backtest import run
from core.feature_store import FeatureStore
from core.metrics import StepUpModel, calibration_ledger, grade
from core.model import Adjudicator
from core.policy import PolicyConfig
from core.report import best_cap
from core.truth import TruthVault

SEEDS = [42, 1, 2, 3, 4]
N = 300_000


def main(n_seeds=len(SEEDS)):
    base, su = PolicyConfig(), StepUpModel()
    tmp = tempfile.mkdtemp(prefix="seedcheck_")
    rows, meta = [], []

    hdr = (f'{"seed":>5}{"holdout":>9}{"locAUC":>9}{"netAUC":>9}{"lift":>8}'
           f'{"prec":>8}{"recall":>8}{"contrib":>11}{"moat":>10}{"moat%":>8}')
    print(hdr)
    print("-" * len(hdr))

    for seed in SEEDS[:n_seeds]:
        out = "data300k" if seed == 42 and os.path.isdir("data300k") else \
              os.path.join(tmp, f"s{seed}")
        if not os.path.isdir(out):
            subprocess.run([sys.executable, "datagen/generate.py", "--n", str(N),
                            "--seed", str(seed), "--out", out],
                           check=True, stdout=subprocess.DEVNULL)

        store, vault = FeatureStore.load(out), TruthVault(out)
        ho = store.split("holdout")
        y = vault.labels(store.payment_ids(ho))

        res = {}
        for blocks in [("local",), ("local", "network")]:
            m = Adjudicator(blocks).fit(store, vault)
            cap = best_cap(calibration_ledger(store, m, base), vault, base, su)
            g = grade(run(store, m, base, "holdout").redecide(base.for_merchant(cap)),
                      vault, su)
            res[blocks] = (roc_auc_score(y, m.predict(store, ho)), g)

        la, lg = res[("local",)]
        na, ng = res[("local", "network")]
        moat = ng.net_contribution_inr - lg.net_contribution_inr
        rows.append((na - la, ng.precision, ng.recall_recoverable,
                     ng.net_contribution_inr, moat, 100 * moat / lg.net_contribution_inr))
        meta.append((seed, len(ho), la, na, na - la, ng.precision, ng.net_contribution_inr))
        print(f'{seed:>5}{len(ho):>9}{la:>9.4f}{na:>9.4f}{na-la:>8.4f}'
              f'{ng.precision:>8.3f}{ng.recall_recoverable:>8.3f}'
              f'{ng.net_contribution_inr/1e7:>10.2f}cr{moat/1e5:>9.2f}L{rows[-1][5]:>+8.1f}')

    a = np.array(rows)
    print("-" * len(hdr))
    for name, f in [("mean", np.mean), ("sd", np.std), ("min", np.min), ("max", np.max)]:
        print(f'{name:>5}{"":>9}{"":>9}{"":>9}{f(a[:,0]):>8.4f}{f(a[:,1]):>8.3f}'
              f'{f(a[:,2]):>8.3f}{f(a[:,3])/1e7:>10.2f}cr{f(a[:,4])/1e5:>9.2f}L{f(a[:,5]):>+8.1f}')
    print()
    print(f"net contribution varies {a[:,3].min()/1e7:.2f}-{a[:,3].max()/1e7:.2f} cr "
          f"across seeds (sd {a[:,3].std()/1e7:.2f}). Quote the range, not a point.")
    print(f"AUC lift varies {a[:,0].min():.4f}-{a[:,0].max():.4f} (mean {a[:,0].mean():.4f}).")
    print(f"precision is the stable one: {a[:,1].min():.3f}-{a[:,1].max():.3f}.")

    os.makedirs("artifacts", exist_ok=True)
    with open("artifacts/seed_check.json", "w", encoding="utf-8") as fh:
        json.dump({
            "rows": [
                {"seed": s_, "n_holdout": int(n_), "local_auc": float(la_),
                 "net_auc": float(na_), "lift": float(lf), "precision": float(pr),
                 "contribution": float(cn)}
                for s_, n_, la_, na_, lf, pr, cn in meta
            ],
            "lift_mean": float(a[:, 0].mean()), "lift_min": float(a[:, 0].min()),
            "lift_max": float(a[:, 0].max()),
            "precision_min": float(a[:, 1].min()), "precision_max": float(a[:, 1].max()),
            "contribution_min": float(a[:, 3].min()), "contribution_max": float(a[:, 3].max()),
            "contribution_sd": float(a[:, 3].std()),
        }, fh, indent=2)
    print("wrote artifacts/seed_check.json")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else len(SEEDS))
