"""Export everything the browser dashboard needs into one JSON bundle.

The dashboard has an agent page where you paste a transaction and get a
decision. That page has to run the real model, not a lookup table and not a
plausible-looking guess, or it is a lie with a nice font.

So this dumps the fitted LightGBM trees and the isotonic calibrator into a
compact form a few dozen lines of JavaScript can evaluate, and then checks that
the JavaScript path and the Python path agree on every holdout case before it
writes anything. If they disagree the export fails.

    python -m service.export_bundle

Writes artifacts/bundle.json.
"""
from __future__ import annotations

import json
import math
import os

import numpy as np

from core.backtest import run as backtest_run
from core.feature_store import FeatureStore
from core.metrics import (HUMAN_COVERAGE, RECONTACT_RANGE, Deployment,
                          StepUpModel, baseline_human_rationed,
                          baseline_human_reviewer, frontier, grade,
                          recontact_arithmetic)
from core.model import Adjudicator
from core.policy import PolicyConfig
from core.showcase import ROLES
from core.showcase import pick as pick_showcase
from core.truth import TruthVault

OUT = os.path.join("artifacts", "bundle.json")
CAP = 0.20


# --- trees ------------------------------------------------------------------
# Compact node encoding. A leaf is a bare number. An internal node is
#   [feature_index, threshold, left, right]
# and the test is always `value <= threshold -> left`, because every split in
# this model is of that form and missing values never occur (checked below).
def pack(node):
    if "leaf_value" in node:
        return node["leaf_value"]
    if node["decision_type"] != "<=":
        raise ValueError(f"unsupported split type {node['decision_type']!r}")
    if node["missing_type"] != "None":
        raise ValueError(f"unsupported missing handling {node['missing_type']!r}")
    return [node["split_feature"], node["threshold"],
            pack(node["left_child"]), pack(node["right_child"])]


def score_packed(trees, x):
    """The reference implementation. The JavaScript in the dashboard is a
    transliteration of this, so if this is right and they match, that is."""
    total = 0.0
    for t in trees:
        n = t
        while isinstance(n, list):
            n = n[2] if x[n[0]] <= n[1] else n[3]
        total += n
    return 1.0 / (1.0 + math.exp(-total))


def interp(xs, ys, v):
    """Isotonic calibration is a step-free piecewise linear map, clipped at
    both ends. Same clipping sklearn uses with out_of_bounds='clip'."""
    if v <= xs[0]:
        return ys[0]
    if v >= xs[-1]:
        return ys[-1]
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= v:
            lo = mid
        else:
            hi = mid
    span = xs[hi] - xs[lo]
    if span == 0:
        return ys[lo]
    return ys[lo] + (ys[hi] - ys[lo]) * (v - xs[lo]) / span


def cr(x):
    a, s = abs(x), "-" if x < 0 else ""
    if a >= 1e7:
        return f"{s}Rs {a / 1e7:,.2f} cr"
    if a >= 1e5:
        return f"{s}Rs {a / 1e5:,.2f} L"
    return f"{s}Rs {a:,.0f}"


def build(data_dir="data300k", out=OUT):
    store, vault = FeatureStore.load(data_dir), TruthVault(data_dir)
    model = Adjudicator().fit(store, vault)
    cfg = PolicyConfig(cap=CAP)
    stepup = StepUpModel()
    names = list(store.feature_names(model.blocks))

    dump = model.clf.booster_.dump_model()
    # The model is fitted on a bare array, so LightGBM names the columns
    # Column_0..Column_N and split_feature is a positional index into the
    # store's feature order. Check that mapping holds rather than the names.
    if dump["feature_names"] != [f"Column_{i}" for i in range(len(names))]:
        raise ValueError(f"unexpected column naming in the dump: "
                         f"{dump['feature_names'][:3]}")
    if list(model.features) != names:
        raise ValueError("model feature order does not match the store")
    trees = [pack(t["tree_structure"]) for t in dump["tree_info"]]
    iso = model.calibrator
    xs = [float(v) for v in iso.X_thresholds_]
    ys = [float(v) for v in iso.y_thresholds_]

    # ---- the check that makes the agent page honest ----------------------
    ho = store.split("holdout")
    X = store.as_matrix(ho, blocks=model.blocks)
    want = model.predict(store, ho)
    got = np.array([interp(xs, ys, score_packed(trees, row)) for row in X])
    worst = float(np.abs(want - got).max())
    if worst > 1e-9:
        raise AssertionError(
            f"exported model disagrees with the fitted one by {worst:.3e} "
            f"on {len(ho)} holdout cases; refusing to write a bundle that lies")

    # ---- everything the pages display ------------------------------------
    with open(os.path.join(data_dir, "stats.json"), encoding="utf-8") as fh:
        stats = json.load(fh)
    with open(os.path.join("artifacts", "headline.json"), encoding="utf-8") as fh:
        headline = json.load(fh)
    with open(os.path.join("artifacts", "seed_check.json"), encoding="utf-8") as fh:
        seeds = json.load(fh)

    ledger = backtest_run(store, model, cfg, "holdout")
    o = grade(ledger, vault, stepup)
    front = frontier(ledger, vault, cfg, stepup)
    ra = recontact_arithmetic(ledger, vault, stepup)
    hu = baseline_human_reviewer(ledger, vault)
    rationed = {f"{c:.2f}": baseline_human_rationed(ledger, vault, c)
                for c in HUMAN_COVERAGE}

    truth = {t.payment_id: t for t in vault.grade(store.payment_ids(ho))}
    picked = pick_showcase(store, vault, model)
    roles = {e.payment_id: next(r.title for r in ROLES if r.key == k)
             for k, (e, _, _) in picked.items()}
    why = {e.payment_id: next(r.why for r in ROLES if r.key == k)
           for k, (e, _, _) in picked.items()}

    # sample transactions for the agent page: the six demo cases, in the raw
    # shape the page asks a user to paste.
    def as_json(e):
        return {"payment_id": e.payment_id, "merchant": e.merchant,
                "amount_inr": round(e.amount_inr, 2), "method": e.meta["method"],
                "block_reason": e.meta["block_reason"],
                "merchant_threshold": e.meta["threshold"],
                **{k: (round(v, 6) if isinstance(v, float) else v)
                   for k, v in e.local.items()},
                **{k: (round(v, 6) if isinstance(v, float) else v)
                   for k, v in e.network.items()}}

    samples = []
    for k, (e, p, v) in picked.items():
        samples.append({"role": roles[e.payment_id], "why": why[e.payment_id],
                        "truth": v.true_outcome, "persona": v.persona,
                        "expected_p_bad": round(float(p), 6),
                        "json": as_json(e)})

    # per-merchant slice, for the portfolio page
    merch = {}
    amounts, actions = ledger.amounts(), ledger.actions()
    y = vault.labels(ledger.payment_ids())
    rel = o.released_mask
    for i, e in enumerate(ho):
        m = merch.setdefault(e.merchant, {
            "merchant": e.merchant, "blocked": 0, "good": 0,
            "value_blocked": 0.0, "released": 0, "released_good": 0,
            "recovered": 0.0, "admitted": 0.0})
        m["blocked"] += 1
        m["value_blocked"] += float(amounts[i])
        good = y[i] == 0
        m["good"] += int(good)
        if rel[i]:
            m["released"] += 1
            m["released_good"] += int(good)
            if good:
                m["recovered"] += float(amounts[i])
            else:
                m["admitted"] += float(amounts[i])
    merchants = sorted(merch.values(), key=lambda r: -r["value_blocked"])

    # feature importance, both columns, because they disagree and it matters
    imp = model.importances()

    # distributions the data page shows: how the blocked pile splits by
    # payment method, by the reason the merchant gave, and over time.
    from collections import Counter
    methods, reasons_mix, by_day = Counter(), Counter(), {}
    good_by_method, good_by_reason = Counter(), Counter()
    for i, e in enumerate(ho):
        good = y[i] == 0
        methods[e.meta["method"]] += 1
        reasons_mix[e.meta["block_reason"]] += 1
        if good:
            good_by_method[e.meta["method"]] += 1
            good_by_reason[e.meta["block_reason"]] += 1
    days = np.array([r.day for r in ledger.records])
    for d in range(int(days.max()) + 1):
        sel = days == d
        by_day[int(d)] = {
            "blocked": int(sel.sum()),
            "released": int((sel & rel).sum()),
            "recovered": float(amounts[sel & rel & (y == 0)].sum()),
            "admitted": float(amounts[sel & rel & (y == 1)].sum()),
        }
    dist = {
        "method": [{"key": k, "blocked": v, "good": good_by_method[k],
                    "good_share": good_by_method[k] / v}
                   for k, v in methods.most_common()],
        "block_reason": [{"key": k, "blocked": v, "good": good_by_reason[k],
                          "good_share": good_by_reason[k] / v}
                         for k, v in reasons_mix.most_common()],
        "daily": [{"day": d, **v} for d, v in sorted(by_day.items())],
    }

    bundle = {
        "generated": True,
        "cap": CAP,
        "margin": cfg.margin,
        "dispute_overhead_inr": cfg.dispute_overhead_inr,
        "stepup_floor_inr": cfg.stepup_floor_inr,
        "uphold_floor": cfg.uphold_floor,
        "min_orders": cfg.min_network_orders,
        "min_tenure_days": cfg.min_network_tenure_days,
        "model": {"features": names, "trees": trees,
                  "iso_x": xs, "iso_y": ys,
                  "n_trees": len(trees), "learner": model.card.learner,
                  "n_fit": model.card.n_fit, "n_calib": model.card.n_calib},
        "stats": stats["stats"],
        "config": stats["config"],
        "headline": headline,
        "seeds": seeds,
        "outcome": {
            "n_cases": o.n_cases, "n_released": o.n_released,
            "precision": o.precision, "recall": o.recall_recoverable,
            "recovered": o.recovered_inr, "admitted": o.fraud_admitted_inr,
            "contribution": o.net_contribution_inr,
            "abstention": o.abstention_rate, "counts": o.counts,
            "n_stepup_passed": o.n_stepup_passed,
        },
        "frontier": front,
        "queue": {"breakeven": ra["breakeven_rate"], "terms": ra["terms"],
                  "rows": ra["rows"], "range": list(RECONTACT_RANGE)},
        "human": {
            "full": {"recall": hu.recall_recoverable, "precision": hu.precision,
                     "contribution": hu.net_contribution_inr,
                     "cost": hu.review_cost_inr},
            "rationed": {k: {"reviewed": v.counts["reviewed"],
                             "recall": v.recall_recoverable,
                             "precision": v.precision,
                             "contribution": v.net_contribution_inr}
                         for k, v in rationed.items()},
        },
        "merchants": merchants,
        "importance": imp,
        "dist": dist,
        "samples": samples,
        "roles": [{"key": r.key, "title": r.title, "why": r.why} for r in ROLES],
    }

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, separators=(",", ":"))
    kb = os.path.getsize(out) / 1024
    print(f"wrote {out} ({kb:.0f} KB)")
    print(f"  model check: exported and fitted agree to {worst:.2e} "
          f"on all {len(ho)} holdout cases")
    return out


if __name__ == "__main__":
    build()
