"""Grading the ledger against the answer key.

Second of the two modules that open ground truth, and the only one with
unrestricted access. Confidence intervals throughout, because the holdout is
small.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .backtest import Ledger
from .policy import Action, PolicyConfig

REVIEW_COST_INR = 150.0     # what a human adjudication used to cost, per case
BOOTSTRAP_B = 2000
BOOT_SEED = 20260826


# ---------------------------------------------------------------------------
# Step-up: the one assumption the dataset cannot supply
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StepUpModel:
    """Verification pass rates. The data cannot supply these, so they are
    declared parameters and swept in METRICS.md 5."""
    p_pass_given_good: float = 0.90
    p_pass_given_bad: float = 0.08

    def label(self) -> str:
        return f"good={self.p_pass_given_good:.2f}/bad={self.p_pass_given_bad:.2f}"


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------
@dataclass
class Outcome:
    n_cases: int
    n_released: int
    n_stepup_passed: int
    precision: float
    recall_recoverable: float
    recovered_inr: float
    fraud_admitted_inr: float
    review_cost_inr: float
    net_inr: float
    net_contribution_inr: float
    abstention_rate: float
    escalation_rate: float
    counts: dict = field(default_factory=dict)
    released_mask: np.ndarray = field(default=None, repr=False)


def _resolve_released(ledger: Ledger, y_bad, stepup, seed=7):
    actions = ledger.actions()
    released = actions == Action.OVERTURN.value
    stepped = actions == Action.STEP_UP.value
    n_passed = 0
    if stepup is not None and stepped.any():
        rng = np.random.default_rng(seed)
        draws = rng.random(len(y_bad))
        p_pass = np.where(y_bad == 0, stepup.p_pass_given_good,
                          stepup.p_pass_given_bad)
        passed = stepped & (draws < p_pass)
        released = released | passed
        n_passed = int(passed.sum())
    return released, n_passed


def grade(ledger: Ledger, vault, stepup: StepUpModel | None = None,
          seed: int = 7) -> Outcome:
    """Join truth after the run completes and price every decision."""
    pids = ledger.payment_ids()
    y_bad = vault.labels(pids)                    # 1 = the block was correct
    amounts = ledger.amounts()
    actions = ledger.actions()
    cfg = ledger.config

    released, n_passed = _resolve_released(ledger, y_bad, stepup, seed)
    stepped = actions == Action.STEP_UP.value
    escalated = actions == Action.ESCALATE.value

    good = y_bad == 0
    rel_good, rel_bad = released & good, released & ~good

    recovered = float(amounts[rel_good].sum())
    admitted = float(amounts[rel_bad].sum())
    # Escalations cost human time; step-ups are automated.
    reviews = float(escalated.sum() * REVIEW_COST_INR)
    n_rel = int(released.sum())

    return Outcome(
        n_cases=len(pids),
        n_released=n_rel,
        n_stepup_passed=n_passed,
        precision=float(rel_good.sum() / n_rel) if n_rel else float("nan"),
        recall_recoverable=float(rel_good.sum() / good.sum()) if good.any() else float("nan"),
        recovered_inr=recovered,
        fraud_admitted_inr=admitted,
        review_cost_inr=reviews,
        net_inr=recovered - admitted - reviews,
        # Margin on recovered sales, minus the whole basket plus overhead on
        # every bad release.
        net_contribution_inr=(cfg.margin * recovered - admitted
                              - cfg.dispute_overhead_inr * int(rel_bad.sum())
                              - reviews),
        abstention_rate=float((stepped | escalated).sum() / len(pids)),
        escalation_rate=float(escalated.sum() / len(pids)),
        counts=ledger.counts(),
        released_mask=released,
    )


_BOOT_ATTRS = ("precision", "recall_recoverable", "recovered_inr",
               "fraud_admitted_inr", "net_inr", "net_contribution_inr",
               "abstention_rate")


def bootstrap_ci(ledger: Ledger, vault, attr: str, stepup=None,
                 B: int = BOOTSTRAP_B, seed: int = BOOT_SEED,
                 clusters=None) -> tuple:
    """Percentile bootstrap.

    Resampling is by customer, not by case, when `clusters` is supplied. About
    36% of holdout cases belong to a customer who appears more than once, and
    those cases are not independent -- they share a network file, a device and a
    persona. Resampling cases independently understates the variance. Passing
    the customer id per case fixes that; the intervals get wider and are right.
    """
    if attr not in _BOOT_ATTRS:
        raise KeyError(f"{attr} not bootstrappable; have {_BOOT_ATTRS}")
    pids = ledger.payment_ids()
    y_bad = vault.labels(pids)
    amounts = ledger.amounts()
    actions = ledger.actions()
    cfg = ledger.config

    released, _ = _resolve_released(ledger, y_bad, stepup)
    escalated = actions == Action.ESCALATE.value
    stepped = actions == Action.STEP_UP.value
    good = y_bad == 0

    rng = np.random.default_rng(seed)
    n = len(pids)

    if clusters is not None:
        clusters = np.asarray(clusters)
        groups = [np.flatnonzero(clusters == c) for c in np.unique(clusters)]
        n_groups = len(groups)

    vals = np.empty(B)
    for b in range(B):
        if clusters is None:
            i = rng.integers(0, n, n)
        else:
            pick = rng.integers(0, n_groups, n_groups)
            i = np.concatenate([groups[k] for k in pick])
        rel, g, amt = released[i], good[i], amounts[i]
        rg, rb = rel & g, rel & ~g
        rec, adm = amt[rg].sum(), amt[rb].sum()
        rev = escalated[i].sum() * REVIEW_COST_INR
        m = len(i)
        if attr == "precision":
            vals[b] = rg.sum() / rel.sum() if rel.sum() else np.nan
        elif attr == "recall_recoverable":
            vals[b] = rg.sum() / g.sum() if g.sum() else np.nan
        elif attr == "recovered_inr":
            vals[b] = rec
        elif attr == "fraud_admitted_inr":
            vals[b] = adm
        elif attr == "net_inr":
            vals[b] = rec - adm - rev
        elif attr == "net_contribution_inr":
            vals[b] = cfg.margin * rec - adm - cfg.dispute_overhead_inr * rb.sum() - rev
        else:
            vals[b] = (stepped[i] | escalated[i]).sum() / m
    return (float(np.nanpercentile(vals, 2.5)), float(np.nanpercentile(vals, 97.5)))


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------
def baseline_do_nothing(ledger: Ledger, vault) -> Outcome:
    """Status quo: the pile is left alone."""
    return _forced(ledger, vault, np.zeros(len(ledger), dtype=bool))


def baseline_release_all(ledger: Ledger, vault) -> Outcome:
    """Upper bound on recovery."""
    return _forced(ledger, vault, np.ones(len(ledger), dtype=bool))


def _forced(ledger: Ledger, vault, released: np.ndarray) -> Outcome:
    pids = ledger.payment_ids()
    y_bad = vault.labels(pids)
    amounts = ledger.amounts()
    cfg = ledger.config
    good = y_bad == 0
    rg, rb = released & good, released & ~good
    rec, adm = float(amounts[rg].sum()), float(amounts[rb].sum())
    n_rel = int(released.sum())
    return Outcome(
        n_cases=len(pids), n_released=n_rel, n_stepup_passed=0,
        precision=float(rg.sum() / n_rel) if n_rel else float("nan"),
        recall_recoverable=float(rg.sum() / good.sum()),
        recovered_inr=rec, fraud_admitted_inr=adm, review_cost_inr=0.0,
        net_inr=rec - adm,
        net_contribution_inr=(cfg.margin * rec - adm
                              - cfg.dispute_overhead_inr * int(rb.sum())),
        abstention_rate=0.0, escalation_rate=0.0,
        counts={}, released_mask=released,
    )


# ---------------------------------------------------------------------------
# Operating points, chosen off-holdout
# ---------------------------------------------------------------------------
CAP_GRID = (0.005, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.125, 0.15, 0.175, 0.20)


def calibration_ledger(store, model, cfg: PolicyConfig) -> Ledger:
    """Ledger over the calibration slice, the last 20% of train. The model was
    not fitted on it, so it is where operating points get chosen."""
    from .backtest import Ledger as _L, LedgerRecord
    from .model import CALIB_FRACTION

    train = store.split("train")
    calib = train[int(len(train) * (1 - CALIB_FRACTION)):]
    probs = model.predict(store, calib)
    t0 = calib[0].created_at
    recs = [
        LedgerRecord(
            seq=i, payment_id=e.payment_id, created_at=e.created_at,
            day=(e.created_at - t0) // 86_400, merchant=e.merchant,
            amount_inr=e.amount_inr, block_reason=e.meta["block_reason"],
            p_bad=float(p),
            network_orders_prior=e.network["network_orders_prior"],
            network_tenure_days=e.network["network_tenure_days"],
            action=Action.UPHOLD.value, ev_release_inr=0.0,
            evidence_sufficient=True, reasons=(),
        )
        for i, (e, p) in enumerate(zip(calib, probs))
    ]
    return _L(recs, cfg, "calib", tuple(model.blocks)).redecide(cfg)


def select_caps(calib: Ledger, vault, base_cfg: PolicyConfig,
                stepup: StepUpModel | None = None) -> dict:
    """Per-merchant risk appetite, fitted on the calibration slice only."""
    from .backtest import Ledger as _L
    caps = {}
    for merchant in sorted({r.merchant for r in calib.records}):
        recs = [r for r in calib.records if r.merchant == merchant]
        sub = _L(recs, base_cfg, calib.split, calib.blocks)
        best, best_net = base_cfg.cap, -np.inf
        for cap in CAP_GRID:
            net = grade(sub.redecide(base_cfg.for_merchant(cap)), vault,
                        stepup).net_contribution_inr
            if net > best_net:
                best, best_net = cap, net
        caps[merchant] = best
    return caps


def apply_per_merchant(ledger: Ledger, caps: dict, base_cfg: PolicyConfig) -> Ledger:
    """Same policy function, one cap per merchant."""
    from .backtest import Ledger as _L
    out = []
    for merchant, cap in caps.items():
        recs = [r for r in ledger.records if r.merchant == merchant]
        if not recs:
            continue
        out.extend(_L(recs, base_cfg.for_merchant(cap), ledger.split,
                      ledger.blocks).redecide(base_cfg.for_merchant(cap)).records)
    missing = [r for r in ledger.records if r.merchant not in caps]
    out.extend(_L(missing, base_cfg, ledger.split, ledger.blocks).redecide(base_cfg).records
               if missing else [])
    out.sort(key=lambda r: r.seq)
    return _L(out, base_cfg, ledger.split, ledger.blocks)


# ---------------------------------------------------------------------------
# The frontier
# ---------------------------------------------------------------------------
def frontier(ledger: Ledger, vault, base_cfg: PolicyConfig,
             stepup: StepUpModel | None = None, caps=CAP_GRID) -> list[dict]:
    rows = []
    for cap in caps:
        o = grade(ledger.redecide(base_cfg.for_merchant(cap)), vault, stepup)
        rows.append({
            "cap": cap, "released": o.n_released, "precision": o.precision,
            "recall": o.recall_recoverable, "recovered": o.recovered_inr,
            "admitted": o.fraud_admitted_inr, "net": o.net_inr,
            "contribution": o.net_contribution_inr,
            "abstention": o.abstention_rate,
        })
    return rows


def ev_release_ceiling(cfg: PolicyConfig) -> float:
    """Largest p_bad an EV rule can release, ignoring the flat fee.

    EV > 0  <=>  (1-p) m A > p (A + f)  ->  p < m/(1+m)  as f/A -> 0.

    So `cap` above m/(1+m) is inert. At m=0.25 the ceiling is 0.20.
    """
    return cfg.margin / (1.0 + cfg.margin)
