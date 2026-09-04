#!/usr/bin/env python3
"""
Does the appeal queue actually contain the signal RECLAIMIFY claims?

Two things must be true or the whole thesis is fiction:
  1. The blocked pile is separable at all (good vs bad is learnable).
  2. Cross-merchant NETWORK features add lift over merchant-LOCAL features.
     If they don't, the "only the platform can build this" moat is a story.

Time-based split. No peeking: the answer key is joined only for scoring.
"""
import gzip, json, sys
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, average_precision_score, precision_recall_curve

DATA = sys.argv[1] if len(sys.argv) > 1 else "./data"


def read(name):
    with gzip.open(f"{DATA}/{name}.jsonl.gz", "rt") as fh:
        return [json.loads(l) for l in fh]


dec = [d for d in read("risk_decisions") if d["action"] == "block"]
truth = {t["payment_id"]: t for t in read("ground_truth")}

LOCAL = ["f_device_is_new", "f_device_account_fanout", "f_address_mismatch",
         "f_orders_last_24h", "f_amount_z", "f_pincode_rto_propensity",
         "f_is_night", "f_thin_file_flag", "f_disposable_email",
         "f_international", "f_merchant_prior_rto", "f_is_cod",
         "risk_score", "amount"]

NETWORK = ["network_orders_prior", "network_merchants_prior", "network_tenure_days",
           "network_clean_rate", "network_disputes_prior", "network_rto_prior",
           "network_device_fanout"]


def matrix(rows, cols):
    return np.array([[float(r[c]) for c in cols] for r in rows])


# label: 1 = this block was CORRECT (order really was bad), 0 = false positive
y = np.array([0 if truth[r["payment_id"]]["true_outcome"] == "clean" else 1 for r in dec])
split = np.array([r["split"] for r in dec])
tr, te = split == "train", split == "holdout"

print(f"appeal queue: {len(dec)}  train={tr.sum()}  holdout={te.sum()}")
print(f"base rate (share of blocks that were correct): {y.mean():.3f}\n")

results = {}
for name, cols in [("merchant-local only", LOCAL),
                   ("local + NETWORK", LOCAL + NETWORK),
                   ("network only", NETWORK)]:
    X = matrix(dec, cols)
    clf = GradientBoostingClassifier(random_state=0, n_estimators=220, max_depth=3,
                                     learning_rate=0.06)
    clf.fit(X[tr], y[tr])
    p = clf.predict_proba(X[te])[:, 1]
    auc = roc_auc_score(y[te], p)
    ap = average_precision_score(y[te], p)
    results[name] = (auc, ap, p, clf, cols)
    print(f"{name:<22} holdout AUC={auc:.4f}  AP={ap:.4f}")

lift = results["local + NETWORK"][0] - results["merchant-local only"][0]
print(f"\nNETWORK LIFT (AUC): +{lift:.4f}")
if lift < 0.01:
    print("  !! Network features add no meaningful lift. The moat claim fails.")
else:
    print("  OK: cross-merchant evidence is doing real work.")

# What would RECLAIMIFY actually recover? Overturn where P(block was correct) is low.
auc, ap, p, clf, cols = results["local + NETWORK"]
te_rows = [r for r, m in zip(dec, te) if m]
amounts = np.array([r["amount"] for r in te_rows]) / 100.0
y_te = y[te]

print("\noverturn threshold sweep (holdout):")
print(f"{'thr':>6} {'released':>9} {'precision':>10} {'recall_good':>12} "
      f"{'INR recovered':>15} {'INR fraud let in':>17} {'net INR':>13}")
for thr in [0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50]:
    rel = p < thr
    if rel.sum() == 0:
        continue
    good = (y_te[rel] == 0)
    prec = good.mean()
    recall = good.sum() / max(1, (y_te == 0).sum())
    rec_inr = amounts[rel][good].sum()
    bad_inr = amounts[rel][~good].sum()
    print(f"{thr:>6.2f} {rel.sum():>9} {prec:>10.3f} {recall:>12.3f} "
          f"{rec_inr:>15,.0f} {bad_inr:>17,.0f} {rec_inr - bad_inr:>13,.0f}")

# which network features matter
imp = sorted(zip(cols, clf.feature_importances_), key=lambda x: -x[1])[:8]
print("\ntop features:")
for n, v in imp:
    print(f"  {v:.4f}  {n}")
