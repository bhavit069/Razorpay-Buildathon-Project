"""Ground truth access.

Only module that opens ground_truth.jsonl.gz. Two entry points:

    training_labels()   train split only, raises HoldoutPeek otherwise
    grade()             unrestricted, used by metrics.py

test_leakage.py checks that feature_store, policy and backtest never import
this module.
"""
from __future__ import annotations

import gzip
import json
import os
from dataclasses import dataclass

import numpy as np

# fraud_undisputed counts as bad even though no dispute was filed.
CLEAN_OUTCOME = "clean"


@dataclass(frozen=True)
class Verdict:
    """Actual outcome for one payment."""
    payment_id: str
    true_outcome: str
    persona: str
    amount: int          # paise
    split: str
    was_blocked: bool

    @property
    def block_was_correct(self) -> bool:
        return self.true_outcome != CLEAN_OUTCOME


class HoldoutPeek(Exception):
    """Training code asked for a non-train label."""


class TruthVault:
    def __init__(self, data_dir: str):
        path = os.path.join(data_dir, "ground_truth.jsonl.gz")
        if not os.path.exists(path):
            raise FileNotFoundError(f"answer key not found at {path}")
        self._by_payment: dict[str, Verdict] = {}
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                self._by_payment[r["payment_id"]] = Verdict(
                    payment_id=r["payment_id"],
                    true_outcome=r["true_outcome"],
                    persona=r["persona"],
                    amount=r["amount"],
                    split=r["split"],
                    was_blocked=r["was_blocked"],
                )
        self._train_ids = frozenset(
            pid for pid, v in self._by_payment.items() if v.split == "train"
        )

    def __len__(self) -> int:
        return len(self._by_payment)

    # ---- training: train split only --------------------------------------
    def training_labels(self, payment_ids) -> np.ndarray:
        """y = 1 if the block was correct, 0 if it was a false positive."""
        payment_ids = list(payment_ids)
        trespass = [p for p in payment_ids if p not in self._train_ids]
        if trespass:
            raise HoldoutPeek(
                f"{len(trespass)} non-train payment_id(s) requested for training "
                f"(first: {trespass[0]}). Use grade() for holdout."
            )
        return np.array(
            [int(self._by_payment[p].block_was_correct) for p in payment_ids],
            dtype=np.int8,
        )

    # ---- scoring: unrestricted, metrics.py only --------------------------
    def grade(self, payment_ids) -> list[Verdict]:
        return [self._by_payment[p] for p in payment_ids]

    def labels(self, payment_ids) -> np.ndarray:
        return np.array(
            [int(v.block_was_correct) for v in self.grade(payment_ids)], dtype=np.int8
        )
