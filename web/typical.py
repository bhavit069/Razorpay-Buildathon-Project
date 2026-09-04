"""Median feature values over the blocked pile.

The agent page answers "what moved this score" by re-scoring the case with one
field replaced and reporting the change. That needs a reference value, and the
reference has to be stated rather than assumed, or the counterfactual means
nothing. This is the median of each feature over the holdout blocked orders, so
the question the page asks is exactly:

    what would p be if this one field were typical for a blocked order,
    and everything else about the case were unchanged

Kept out of export_bundle.py because that module imports the model and so needs
LightGBM, while this needs only the feature matrix.
"""
from __future__ import annotations

import numpy as np

from core.feature_store import FeatureStore


def typical_values(data_dir: str = "data300k", split: str = "holdout") -> dict:
    fs = FeatureStore.load(data_dir)
    cases = fs.split(split)
    x = fs.as_matrix(cases, blocks=("local", "network"))
    med = np.median(x, axis=0)
    names = FeatureStore.feature_names(("local", "network"))
    return {name: float(v) for name, v in zip(names, med)}


if __name__ == "__main__":  # patch an already-written bundle in place
    import json
    import os
    out = os.path.join("artifacts", "bundle.json")
    with open(out, encoding="utf-8") as fh:
        bundle = json.load(fh)
    bundle["typical"] = typical_values()
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, separators=(",", ":"))
    for k, v in bundle["typical"].items():
        print(f"  {k:30s} {v:>14.4f}")
