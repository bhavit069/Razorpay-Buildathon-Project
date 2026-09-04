"""Leakage checks. ARCHITECTURE.md 1.6.

If any of these fail, nothing in METRICS.md means anything.
"""
import ast
import os

import numpy as np
import pytest
from sklearn.metrics import roc_auc_score

from core import feature_store as fs
from core.model import Adjudicator
from core.truth import HoldoutPeek

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Modules that must have no path to the answer key at all.
QUARANTINED = ["core/feature_store.py", "core/policy.py", "core/backtest.py",
               "core/recovery.py"]


# --- (a) structural: no forbidden join is even reachable ---------------------
@pytest.mark.parametrize("relpath", QUARANTINED)
def test_module_cannot_import_truth(relpath):
    tree = ast.parse(open(os.path.join(ROOT, relpath), encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            if node.level and node.names:
                imported.update(a.name for a in node.names)
    bad = {m for m in imported if m and "truth" in m}
    assert not bad, f"{relpath} imports the answer key: {bad}"


def test_appeal_queue_carries_no_answer_key(store):
    forbidden = {"persona", "true_outcome", "is_false_positive",
                 "is_true_positive", "was_blocked"}
    for e in store.cases[:200]:
        assert not (forbidden & set(e.local))
        assert not (forbidden & set(e.network))
        assert not (forbidden & set(e.meta))


def test_training_door_refuses_holdout(store, vault):
    holdout_ids = store.payment_ids(store.split("holdout"))[:5]
    with pytest.raises(HoldoutPeek):
        vault.training_labels(holdout_ids)


def test_no_single_feature_is_the_label(store, vault):
    """A feature that alone separates truth is a leaked label."""
    cases = store.split("holdout")
    y = vault.labels(store.payment_ids(cases))
    X = store.as_matrix(cases)
    for j, name in enumerate(store.feature_names()):
        col = X[:, j]
        if len(np.unique(col)) < 2:
            continue
        auc = roc_auc_score(y, col)
        auc = max(auc, 1 - auc)
        assert auc < 0.99, f"{name} alone scores AUC {auc:.4f}"


# --- (b) shuffle test: signal must vanish under permuted labels --------------
def test_shuffled_labels_collapse_to_chance(store, vault, monkeypatch):
    cases_tr = store.split("train")
    y_tr = vault.training_labels(store.payment_ids(cases_tr))
    rng = np.random.default_rng(0)
    y_shuf = rng.permutation(y_tr)

    class ShuffledVault:
        def training_labels(self, pids):
            return y_shuf[: len(pids)] if len(pids) <= len(y_shuf) else y_shuf

    m = Adjudicator()
    m.fit(store, _SliceVault(y_shuf))
    ho = store.split("holdout")
    p = m.predict(store, ho)
    y_ho = vault.labels(store.payment_ids(ho))
    auc = roc_auc_score(y_ho, p)
    assert 0.40 < auc < 0.60, (
        f"holdout AUC {auc:.3f} on permuted training labels"
    )


class _SliceVault:
    """Serves permuted labels in the same order fit() asks for them."""

    def __init__(self, y):
        self._y = y
        self._n = 0

    def training_labels(self, pids):
        out = self._y[self._n: self._n + len(pids)]
        self._n += len(pids)
        return out


# --- (c) time machine: features must not know about the future ---------------
def test_network_counters_are_monotone_per_customer(store):
    """Point-in-time counters only go up. A dip means the value was computed
    over a window that saw events it should not have."""
    by_cust = {}
    for e in store.cases:
        by_cust.setdefault(e.meta["customer_id"], []).append(e)

    checked = 0
    for cid, cases in by_cust.items():
        if len(cases) < 2:
            continue
        cases = sorted(cases, key=lambda e: e.created_at)
        for a, b in zip(cases, cases[1:]):
            for f in ("network_orders_prior", "network_disputes_prior",
                      "network_rto_prior", "network_merchants_prior"):
                assert b.network[f] >= a.network[f], (
                    f"{cid}: {f} fell from {a.network[f]} to {b.network[f]} "
                    f"between {a.payment_id} and {b.payment_id}"
                )
            assert b.network["network_tenure_days"] >= a.network["network_tenure_days"]
            checked += 1
    assert checked > 50, "not enough repeat customers to make this test meaningful"


def test_tenure_advances_with_wall_clock(store):
    """Tenure between two orders by the same customer cannot grow faster than
    the elapsed time between them."""
    by_cust = {}
    for e in store.cases:
        by_cust.setdefault(e.meta["customer_id"], []).append(e)
    for cases in by_cust.values():
        if len(cases) < 2:
            continue
        cases = sorted(cases, key=lambda e: e.created_at)
        for a, b in zip(cases, cases[1:]):
            elapsed_days = (b.created_at - a.created_at) / 86_400.0
            grew = b.network["network_tenure_days"] - a.network["network_tenure_days"]
            assert grew <= elapsed_days + 1.5, (
                f"tenure grew {grew:.1f}d over {elapsed_days:.1f}d of wall clock"
            )


def test_store_is_chronological(store):
    ts = [e.created_at for e in store.cases]
    assert ts == sorted(ts)


def test_ablation_is_a_flag_not_a_fork(store):
    """Both ablation arms must run the same code."""
    cases = store.split("holdout")[:50]
    assert store.as_matrix(cases, ("local",)).shape[1] == len(fs.LOCAL_FEATURES)
    assert store.as_matrix(cases, ("network",)).shape[1] == len(fs.NETWORK_FEATURES)
    assert store.as_matrix(cases, ("local", "network")).shape[1] == (
        len(fs.LOCAL_FEATURES) + len(fs.NETWORK_FEATURES))
    with pytest.raises(KeyError):
        store.as_matrix(cases, ("local", "psychic"))
