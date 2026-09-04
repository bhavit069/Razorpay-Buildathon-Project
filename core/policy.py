"""Per-case expected-value decision rule.

    EV(release) = (1 - p_bad) * m * A  -  p_bad * (A + f)  -  c_review

    A         order amount (INR)
    m         merchant contribution margin (default 0.25)
    f         dispute / ops overhead on a bad release
    c_review  cost of adjudicating the case, ~0 here (it was Rs 150+ of human
              time when a person did it)

Two consequences:

  1. The same p_bad gives different actions at different amounts, because A
     appears on both sides of the inequality.
  2. Sweeping `cap` produces the operating-point frontier. It is this function
     in a loop, not a separate calculation.

The insufficiency gate fires on evidence quantity, not model confidence, and
guards the release path only. See decide().

Must not import core.truth.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class Action(str, Enum):
    OVERTURN = "OVERTURN"   # release to checkout
    UPHOLD = "UPHOLD"       # the block stands
    STEP_UP = "STEP_UP"     # generate new evidence, then re-decide
    ESCALATE = "ESCALATE"   # human review, with a written brief

    @property
    def releases_money(self) -> bool:
        return self is Action.OVERTURN


@dataclass(frozen=True)
class PolicyConfig:
    margin: float = 0.25            # m: contribution margin on a recovered sale
    # f: cost of a bad release beyond the goods. Indian card chargebacks run
    # Rs 500-1500 in fees plus ops; an RTO on a small COD order costs less.
    # Swept at 250 / 750 / 1500 in METRICS.md 5.
    dispute_overhead_inr: float = 750.0
    review_cost_inr: float = 0.0    # c_review: ~0 for the agent
    cap: float = 0.20               # merchant risk appetite: max tolerable p_bad
    uphold_floor: float = 0.55      # above this, close the case
    stepup_floor_inr: float = 5_000.0       # below this, skip the exchange
    min_network_orders: int = 3     # the insufficiency gate
    min_network_tenure_days: int = 30
    # ARCHITECTURE.md 1.3 sends thin-file small cases to ESCALATE. That spends
    # the expensive path on the cheapest cases; left as specified but exposed.
    insufficient_small_action: Action = Action.ESCALATE

    def for_merchant(self, cap: float) -> "PolicyConfig":
        return replace(self, cap=cap)


@dataclass(frozen=True)
class Decision:
    payment_id: str
    action: Action
    p_bad: float
    amount_inr: float
    ev_release_inr: float
    reasons: tuple           # rules that fired; the verdict writer cites these
    evidence_sufficient: bool

    @property
    def released(self) -> bool:
        return self.action.releases_money


def ev_release(p_bad: float, amount_inr: float, cfg: PolicyConfig) -> float:
    """Expected rupees from releasing this order. Negative means don't."""
    upside = (1.0 - p_bad) * cfg.margin * amount_inr
    downside = p_bad * (amount_inr + cfg.dispute_overhead_inr)
    return upside - downside - cfg.review_cost_inr


@dataclass(frozen=True)
class Verification:
    """Outcome of a step-up exchange, as an input to the gate.

    Verification is not history. Confirming an address shows someone controls
    the account; it does not create a payment record, so it must not be written
    into the network features. Doing that falsifies the file, and because the
    model reads order counts as shape it can make a customer who verified score
    worse.

    It enters at the gate instead, the part that asks whether we can identify
    this person. p_bad is untouched.
    """
    identity_confirmed: bool = False
    prepaid_accepted: bool = False
    confirmations: int = 0

    @property
    def clears_gate(self) -> bool:
        return self.identity_confirmed and self.confirmations >= 2


def evidence_sufficient(evidence, cfg: PolicyConfig,
                        verification: "Verification | None" = None) -> tuple[bool, tuple]:
    """Is there enough here to act on? Quantity of record, or a verified identity."""
    n = evidence.network["network_orders_prior"]
    tenure = evidence.network["network_tenure_days"]
    reasons = []
    if n < cfg.min_network_orders:
        reasons.append(f"thin_network_file(orders={n:.0f}<{cfg.min_network_orders})")
    if tenure < cfg.min_network_tenure_days:
        reasons.append(f"short_tenure(days={tenure:.0f}<{cfg.min_network_tenure_days})")
    if reasons and verification is not None and verification.clears_gate:
        return True, (f"identity_verified({verification.confirmations}_confirmations)",)
    return (not reasons), tuple(reasons)


def decide(p_bad: float, evidence, cfg: PolicyConfig = PolicyConfig(),
           verification: "Verification | None" = None) -> Decision:
    A = evidence.amount_inr
    ev = ev_release(p_bad, A, cfg)
    sufficient, gate_reasons = evidence_sufficient(evidence, cfg, verification)
    big_enough = A >= cfg.stepup_floor_inr

    # Confidence check runs before the insufficiency gate. ARCHITECTURE.md 1.3
    # has it the other way round, which sent a p_bad=1.000 case into a step-up
    # dialogue that could have released it. Upholding needs no exculpatory
    # evidence since the block is already in place, so the gate only has to
    # guard releases.
    if p_bad > cfg.uphold_floor:
        action = Action.UPHOLD
        reasons = (f"confidently_bad(p_bad={p_bad:.3f}>{cfg.uphold_floor})",)
        if not sufficient:
            reasons += ("thin_file_but_uphold_needs_no_exculpation",) + gate_reasons
    elif not sufficient:
        action = Action.STEP_UP if big_enough else cfg.insufficient_small_action
        reasons = ("insufficient_evidence",) + gate_reasons + (
            f"amount_{'above' if big_enough else 'below'}_stepup_floor",)
    elif ev > 0 and p_bad < cfg.cap:
        action = Action.OVERTURN
        reasons = (f"ev_positive({ev:+,.0f})", f"p_bad_under_cap({p_bad:.3f}<{cfg.cap})")
        if verification is not None and verification.clears_gate:
            reasons += gate_reasons
    else:
        action = Action.STEP_UP if big_enough else Action.UPHOLD
        reasons = ("ambiguous",
                   f"ev={ev:+,.0f}", f"p_bad={p_bad:.3f}",
                   f"amount_{'above' if big_enough else 'below'}_stepup_floor")

    return Decision(
        payment_id=evidence.payment_id, action=action, p_bad=float(p_bad),
        amount_inr=A, ev_release_inr=ev, reasons=reasons,
        evidence_sufficient=sufficient,
    )


# Fallback only. metrics.select_caps() fits these on the calibration slice.
MERCHANT_CAPS = {
    "Aurum Jewels": 0.05, "Voltcart": 0.10, "HomeHaul": 0.15, "Threadline": 0.20,
    "Kirana Direct": 0.30, "PharmaNow": 0.30, "BookNest": 0.40, "SnackBox": 0.40,
}
