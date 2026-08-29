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
# Deployment mode: where in the flow this runs
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Deployment:
    """Where the review sits, which decides whether a release is worth money.

    INLINE means the review runs at checkout while the customer is still in
    session. A release converts at full order value, and a step-up has to
    complete inside that session.

    QUEUE means the pile is reviewed afterwards and the customer is invited
    back. They have already left. Only some return, so booking full order value
    overstates recovery, and the step-up exchange is asynchronous.

    ASSUMPTION, declared rather than buried: only good releases are discounted.
    A fraudster invited back to finish a stolen-instrument order is more
    motivated to return than an honest customer who has already bought
    elsewhere, so fraud admitted is booked in full at every rate. This is not
    measured, it is asserted, and it is the pessimistic direction. If honest and
    fraudulent customers returned at the same rate, the queue figures below
    would be better than reported, not worse.

    The rate itself is not one number. An in-session retry prompt is close to
    inline; a next-day email is not. RECONTACT_RANGE is reported as a band for
    that reason, and the breakeven rate is reported alongside it, since that is
    the figure that does not depend on guessing the right point in the band.
    """
    mode: str = "inline"
    recontact_rate: float = 1.0        # share of good customers who return

    @staticmethod
    def inline() -> "Deployment":
        return Deployment("inline", 1.0)

    @staticmethod
    def queue(recontact_rate: float = 0.35) -> "Deployment":
        return Deployment("queue", recontact_rate)

    def label(self) -> str:
        if self.mode == "inline":
            return "inline at checkout, full order value"
        return f"queue review, {self.recontact_rate:.0%} of good customers return"


DEPLOYMENT = Deployment.inline()

# Reported as a band. 70% is an in-session retry prompt, 35% a next-day email,
# 50% the midpoint. No single point is defensible so none is presented alone.
RECONTACT_RANGE = (0.70, 0.50, 0.35)


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
          seed: int = 7, deployment: Deployment = DEPLOYMENT) -> Outcome:
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

    # In queue mode only some invited customers come back, so a good release is
    # worth less than the order. Fraud is booked in full either way.
    recovered = float(amounts[rel_good].sum()) * deployment.recontact_rate
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
def baseline_do_nothing(ledger: Ledger, vault,
                        deployment: Deployment = DEPLOYMENT) -> Outcome:
    """Status quo: the pile is left alone."""
    return _forced(ledger, vault, np.zeros(len(ledger), dtype=bool), deployment)


def baseline_release_all(ledger: Ledger, vault,
                         deployment: Deployment = DEPLOYMENT) -> Outcome:
    """Upper bound on recovery."""
    return _forced(ledger, vault, np.ones(len(ledger), dtype=bool), deployment)


HUMAN_ACCURACY = 0.875          # midpoint of the 85-90% range for manual review
HUMAN_COST_INR = 150.0          # per case
HUMAN_LATENCY_HOURS = 4.0


def baseline_human_reviewer(ledger: Ledger, vault, accuracy: float = HUMAN_ACCURACY,
                            seed: int = 11,
                            deployment: Deployment = DEPLOYMENT) -> Outcome:
    """A human adjudicating every case at `accuracy`, at HUMAN_COST_INR each.

    Modelled as a classifier that is right `accuracy` of the time in both
    directions: it releases that share of good cases and, by the same error
    rate, that share of bad ones too. Deliberately generous, since a real queue
    is rationed to high-value cases and never reaches most of this pile at all.
    """
    pids = ledger.payment_ids()
    y_bad = vault.labels(pids)
    rng = np.random.default_rng(seed)
    correct = rng.random(len(pids)) < accuracy
    released = np.where(y_bad == 0, correct, ~correct)
    o = _forced(ledger, vault, released, deployment)
    reviews = HUMAN_COST_INR * len(pids)
    return Outcome(
        **{**vars(o),
           "review_cost_inr": reviews,
           "net_inr": o.net_inr - reviews,
           "net_contribution_inr": o.net_contribution_inr - reviews}
    )


HUMAN_COVERAGE = (0.03, 0.10)   # what a real queue actually gets through


def baseline_human_rationed(ledger: Ledger, vault, coverage: float,
                            accuracy: float = HUMAN_ACCURACY, seed: int = 11,
                            deployment: Deployment = DEPLOYMENT) -> Outcome:
    """A human reviewing only the top `coverage` of the pile by order value.

    This is what merchants actually do. A reviewer costs money and takes hours,
    so the queue is ranked by value and worked down until the day runs out.
    Everything below the line is never looked at, stays blocked, and recovers
    nothing.

    The reviewer is the same one as baseline_human_reviewer, at the same
    accuracy, on the cases they reach. The difference is only how many they
    reach. Review cost is charged for reviewed cases only, which is the whole
    point of rationing.
    """
    pids = ledger.payment_ids()
    y_bad = vault.labels(pids)
    amounts = ledger.amounts()
    n_review = int(round(coverage * len(pids)))

    # Rank by value, work down from the top.
    order = np.argsort(-amounts, kind="stable")
    reviewed = np.zeros(len(pids), dtype=bool)
    reviewed[order[:n_review]] = True

    rng = np.random.default_rng(seed)
    correct = rng.random(len(pids)) < accuracy
    # Unreviewed cases stay blocked: the block already happened, nobody undid it.
    released = reviewed & np.where(y_bad == 0, correct, ~correct)

    o = _forced(ledger, vault, released, deployment)
    reviews = HUMAN_COST_INR * n_review
    o = Outcome(**{**vars(o),
                   "review_cost_inr": reviews,
                   "net_inr": o.net_inr - reviews,
                   "net_contribution_inr": o.net_contribution_inr - reviews})
    o.counts = {"reviewed": n_review, "unreviewed": len(pids) - n_review,
                "coverage": coverage}
    return o


# ---------------------------------------------------------------------------
# Recontact arithmetic, exposed so it can be checked by hand
# ---------------------------------------------------------------------------
def recontact_arithmetic(ledger: Ledger, vault, stepup: StepUpModel | None = None,
                         seed: int = 7, rates=None) -> dict:
    """Every term in the queue-mode contribution, so the drop can be audited.

    Net contribution is  m * R_gross * rate - A - f * n_bad - reviews.
    Only the first term scales with the recontact rate. The rest is fixed drag,
    and that is the whole reason a 35% recontact rate costs far more than 65%
    of the contribution: margin is 25% of a recovered rupee but fraud is 100%
    of an admitted one, so the fixed drag is being subtracted from a quarter of
    a shrinking number.

    Returns the terms and a row per rate. Every row is reproducible with a
    calculator from `terms`.
    """
    rates = tuple(rates) if rates is not None else (1.0,) + RECONTACT_RANGE
    pids = ledger.payment_ids()
    y_bad = vault.labels(pids)
    amounts = ledger.amounts()
    cfg = ledger.config
    released, _ = _resolve_released(ledger, y_bad, stepup, seed)
    escalated = ledger.actions() == Action.ESCALATE.value
    good = y_bad == 0
    rg, rb = released & good, released & ~good

    R = float(amounts[rg].sum())
    A = float(amounts[rb].sum())
    n_bad, n_esc = int(rb.sum()), int(escalated.sum())
    overhead = cfg.dispute_overhead_inr * n_bad
    reviews = REVIEW_COST_INR * n_esc
    drag = A + overhead + reviews

    rows = []
    for rate in rates:
        booked = R * rate
        rows.append({"rate": rate, "booked_inr": booked,
                     "margin_inr": cfg.margin * booked,
                     "drag_inr": drag,
                     "net_contribution_inr": cfg.margin * booked - drag})
    return {
        "terms": {"n_good_released": int(rg.sum()), "R_gross_inr": R,
                  "n_bad_released": n_bad, "fraud_admitted_inr": A,
                  "n_escalated": n_esc, "margin": cfg.margin,
                  "dispute_overhead_inr": cfg.dispute_overhead_inr,
                  "review_cost_inr": REVIEW_COST_INR,
                  "overhead_total_inr": overhead, "reviews_total_inr": reviews,
                  "drag_inr": drag},
        "rows": rows,
        # rate at which margin on recovery exactly covers the fixed drag
        "breakeven_rate": drag / (cfg.margin * R) if R else float("nan"),
    }


def _forced(ledger: Ledger, vault, released: np.ndarray,
            deployment: Deployment = DEPLOYMENT) -> Outcome:
    pids = ledger.payment_ids()
    y_bad = vault.labels(pids)
    amounts = ledger.amounts()
    cfg = ledger.config
    good = y_bad == 0
    rg, rb = released & good, released & ~good
    rec = float(amounts[rg].sum()) * deployment.recontact_rate
    adm = float(amounts[rb].sum())
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


# ---------------------------------------------------------------------------
# Leave-one-merchant-out
# ---------------------------------------------------------------------------
def leave_one_merchant_out(store, vault, merchant: str, base_cfg: PolicyConfig,
                           stepup: StepUpModel | None = None,
                           deployment: Deployment = DEPLOYMENT) -> dict:
    """Train with one merchant removed, then score only that merchant.

    The pitch is a platform product, so the model has to work on a merchant it
    has never seen. Holding one out and testing on it is the only way to know
    that, and it matters most for the merchant carrying most of the value.
    """
    from sklearn.metrics import average_precision_score, roc_auc_score

    from .backtest import Ledger as _L, LedgerRecord
    from .model import Adjudicator

    held_train = [e for e in store.split("train") if e.merchant != merchant]
    held_test = [e for e in store.split("holdout") if e.merchant == merchant]
    if not held_test:
        raise ValueError(f"no holdout cases for {merchant}")

    class _Subset:
        """A store view with the merchant removed from train."""
        source = store.source

        def split(self, name):
            return held_train if name == "train" else store.split(name)

        def as_matrix(self, cases, blocks):
            return store.as_matrix(cases, blocks)

        def payment_ids(self, cases=None):
            return store.payment_ids(cases)

        @staticmethod
        def feature_names(blocks):
            return store.feature_names(blocks)

    lomo = Adjudicator().fit(_Subset(), vault)
    full = Adjudicator().fit(store, vault)

    y = vault.labels(store.payment_ids(held_test))
    out = {"merchant": merchant, "n_train_dropped": len(store.split("train")) - len(held_train),
           "n_test": len(held_test)}

    for tag, mdl in (("lomo", lomo), ("full", full)):
        p = mdl.predict(store, held_test)
        recs = [
            LedgerRecord(seq=i, payment_id=e.payment_id, created_at=e.created_at,
                         day=0, merchant=e.merchant, amount_inr=e.amount_inr,
                         block_reason=e.meta["block_reason"], p_bad=float(pi),
                         network_orders_prior=e.network["network_orders_prior"],
                         network_tenure_days=e.network["network_tenure_days"],
                         action=Action.UPHOLD.value, ev_release_inr=0.0,
                         evidence_sufficient=True, reasons=())
            for i, (e, pi) in enumerate(zip(held_test, p))
        ]
        led = _L(recs, base_cfg, "holdout", tuple(mdl.blocks)).redecide(base_cfg)
        g = grade(led, vault, stepup, deployment=deployment)
        out[tag] = {
            "auc": float(roc_auc_score(y, p)),
            "ap": float(average_precision_score(y, p)),
            "released": g.n_released,
            "precision": g.precision,
            "recall": g.recall_recoverable,
            "contribution": g.net_contribution_inr,
        }
    out["auc_drop"] = out["full"]["auc"] - out["lomo"]["auc"]
    out["contribution_drop"] = out["full"]["contribution"] - out["lomo"]["contribution"]
    return out
