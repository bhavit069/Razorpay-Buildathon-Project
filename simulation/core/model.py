"""P(this block was correct | evidence).

Target      y = 1 if the true outcome was not clean, 0 if the block was a
            false positive. The policy layer consumes p_bad as a price, so it
            needs to be calibrated and not merely well ranked.

Split       Temporal. Fit on the first 80% of train, calibrate on the last
            20%. Hyperparameters are fixed constants; with ~6.5k rows a search
            would fit the calibration slice.

Ablation    blocks=("local",) vs ("local","network") is an argument, not a
            code fork, so both arms run identical code.
"""
from __future__ import annotations

import hashlib
import json
import os
import pickle
from dataclasses import dataclass, asdict

import numpy as np
from sklearn.isotonic import IsotonicRegression

from .feature_store import FeatureStore

try:
    from lightgbm import LGBMClassifier
    _HAVE_LGBM = True
except ImportError:  # sklearn GBM is the documented fallback
    from sklearn.ensemble import GradientBoostingClassifier
    _HAVE_LGBM = False

CALIB_FRACTION = 0.20
SEED = 42


@dataclass(frozen=True)
class ModelCard:
    blocks: tuple
    features: tuple
    n_fit: int
    n_calib: int
    base_rate_fit: float
    learner: str
    seed: int
    feature_hash: str
    source: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=list)


class Adjudicator:
    def __init__(self, blocks=("local", "network")):
        self.blocks = tuple(blocks)
        self.features = tuple(FeatureStore.feature_names(self.blocks))
        self.clf = None
        self.calibrator = None
        self.card: ModelCard | None = None

    # ---- training --------------------------------------------------------
    def fit(self, store: FeatureStore, vault) -> "Adjudicator":
        """vault must be a TruthVault. It is asked only for train-split labels;
        it raises HoldoutPeek if this method ever reaches past the boundary."""
        train = store.split("train")
        if not train:
            raise ValueError("no train-split cases")

        cut = int(len(train) * (1 - CALIB_FRACTION))
        fit_cases, calib_cases = train[:cut], train[cut:]   # already chronological

        X_fit = store.as_matrix(fit_cases, self.blocks)
        X_cal = store.as_matrix(calib_cases, self.blocks)
        y_fit = vault.training_labels(store.payment_ids(fit_cases))
        y_cal = vault.training_labels(store.payment_ids(calib_cases))

        self.clf = _make_learner()
        self.clf.fit(X_fit, y_fit)

        raw_cal = self._raw(X_cal)
        self.calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        self.calibrator.fit(raw_cal, y_cal)

        self.card = ModelCard(
            blocks=self.blocks,
            features=self.features,
            n_fit=len(fit_cases),
            n_calib=len(calib_cases),
            base_rate_fit=float(y_fit.mean()),
            learner="lightgbm" if _HAVE_LGBM else "sklearn-gbm",
            seed=SEED,
            feature_hash=hashlib.sha256("|".join(self.features).encode()).hexdigest()[:16],
            source=store.source,
        )
        return self

    # ---- inference -------------------------------------------------------
    def _raw(self, X: np.ndarray) -> np.ndarray:
        return self.clf.predict_proba(X)[:, 1]

    def predict(self, store: FeatureStore, cases) -> np.ndarray:
        """Calibrated P(block was correct) for each case."""
        return self.predict_matrix(store.as_matrix(cases, self.blocks))

    def predict_matrix(self, X: np.ndarray) -> np.ndarray:
        if X.shape[1] != len(self.features):
            raise ValueError(
                f"expected {len(self.features)} features for blocks {self.blocks}, "
                f"got {X.shape[1]}"
            )
        return np.clip(self.calibrator.predict(self._raw(X)), 0.0, 1.0)

    def predict_one(self, evidence) -> float:
        return float(self.predict_matrix(evidence.as_vector(self.blocks)[None, :])[0])

    def raw_uncalibrated(self, store: FeatureStore, cases) -> np.ndarray:
        return self._raw(store.as_matrix(cases, self.blocks))

    def importances(self, kind: str = "gain") -> list[tuple[str, float]]:
        """Feature importance, normalised to sum to 1.

        LightGBM defaults to split count (how often a feature was used), which
        is nearly flat. `gain` is how much each split improved the objective,
        and is what DATA_CARD 6.4 means when it warns about
        `network_clean_rate`. The two disagree here; METRICS.md 4 shows both.
        """
        if _HAVE_LGBM and kind == "gain":
            imp = np.asarray(
                self.clf.booster_.feature_importance(importance_type="gain"),
                dtype=float)
        else:
            imp = np.asarray(self.clf.feature_importances_, dtype=float)
        if imp.sum():
            imp = imp / imp.sum()
        return sorted(zip(self.features, imp), key=lambda t: -t[1])

    # ---- artifacts -------------------------------------------------------
    def save(self, outdir: str) -> str:
        os.makedirs(outdir, exist_ok=True)
        tag = "_".join(self.blocks)
        with open(os.path.join(outdir, f"model_{tag}.pkl"), "wb") as fh:
            pickle.dump({"clf": self.clf, "calibrator": self.calibrator,
                         "blocks": self.blocks, "features": self.features}, fh)
        with open(os.path.join(outdir, f"model_{tag}.card.json"), "w") as fh:
            fh.write(self.card.to_json())
        return outdir

    @classmethod
    def load(cls, outdir: str, blocks=("local", "network")) -> "Adjudicator":
        tag = "_".join(blocks)
        with open(os.path.join(outdir, f"model_{tag}.pkl"), "rb") as fh:
            blob = pickle.load(fh)
        m = cls(blob["blocks"])
        m.clf, m.calibrator = blob["clf"], blob["calibrator"]
        return m


def _make_learner():
    if _HAVE_LGBM:
        return LGBMClassifier(
            n_estimators=300, num_leaves=15, max_depth=5, learning_rate=0.05,
            min_child_samples=30, subsample=0.9, subsample_freq=1,
            colsample_bytree=0.9, reg_lambda=1.0,
            random_state=SEED, n_jobs=1, verbose=-1, deterministic=True,
            force_row_wise=True,
        )
    return GradientBoostingClassifier(
        n_estimators=220, max_depth=3, learning_rate=0.06, random_state=SEED
    )
