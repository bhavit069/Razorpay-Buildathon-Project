"""Moat isolation in rupees: local-only vs local+network on the same holdout."""
import gzip, json, sys
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier

DATA = sys.argv[1]

def read(name):
    with gzip.open(f"{DATA}/{name}.jsonl.gz", "rt") as fh:
        return [json.loads(l) for l in fh]

dec = [d for d in read("risk_decisions") if d["action"] == "block"]
truth = {t["payment_id"]: t for t in read("ground_truth")}

LOCAL = ["f_device_is_new","f_device_account_fanout","f_address_mismatch","f_orders_last_24h",
         "f_amount_z","f_pincode_rto_propensity","f_is_night","f_thin_file_flag",
         "f_disposable_email","f_international","f_merchant_prior_rto","f_is_cod",
         "risk_score","amount"]
NETWORK = ["network_orders_prior","network_merchants_prior","network_tenure_days",
           "network_clean_rate","network_disputes_prior","network_rto_prior","network_device_fanout"]

y = np.array([0 if truth[r["payment_id"]]["true_outcome"] == "clean" else 1 for r in dec])
split = np.array([r["split"] for r in dec])
tr, te = split == "train", split == "holdout"
amounts = np.array([r["amount"] for r in dec])[te] / 100.0
y_te = y[te]
total_good_inr = amounts[y_te == 0].sum()

preds = {}
for name, cols in [("local", LOCAL), ("local+network", LOCAL + NETWORK)]:
    X = np.array([[float(r[c]) for c in cols] for r in dec])
    clf = GradientBoostingClassifier(random_state=0, n_estimators=220, max_depth=3, learning_rate=0.06)
    clf.fit(X[tr], y[tr])
    preds[name] = clf.predict_proba(X[te])[:, 1]

print(f"holdout blocked={te.sum()}  recoverable ₹{total_good_inr:,.0f}\n")
print(f"{'thr':>5} | {'model':<14} {'rel':>5} {'prec':>6} {'₹ recovered':>14} {'₹ fraud in':>12} {'net ₹':>14}")
print("-" * 78)
for thr in [0.05, 0.10, 0.20, 0.30, 0.40]:
    row = {}
    for name in ("local", "local+network"):
        rel = preds[name] < thr
        good = y_te[rel] == 0
        rec, bad = amounts[rel][good].sum(), amounts[rel][~good].sum()
        row[name] = (rel.sum(), good.mean() if rel.sum() else 0, rec, bad, rec - bad)
        print(f"{thr:>5.2f} | {name:<14} {rel.sum():>5} {row[name][1]:>6.3f} "
              f"{rec:>14,.0f} {bad:>12,.0f} {rec-bad:>14,.0f}")
    d = row["local+network"][4] - row["local"][4]
    print(f"      | {'NETWORK GAIN':<14} {'':>5} {'':>6} {'':>14} {'':>12} {d:>+14,.0f}")
    print("-" * 78)
