"""Policy invariants. ARCHITECTURE.md 1.6."""
import numpy as np
import pytest

from core.policy import (Action, PolicyConfig, decide, ev_release,
                         evidence_sufficient)


class Ev:
    """Synthetic case, so these tests do not need the dataset."""

    def __init__(self, amount_inr, orders=25, tenure=900, pid="pay_test"):
        self.payment_id = pid
        self.amount_inr = amount_inr
        self.network = {"network_orders_prior": orders,
                        "network_tenure_days": tenure}


CFG = PolicyConfig()


# --- EV monotonicity ---------------------------------------------------------
def test_ev_strictly_decreasing_in_p_bad():
    prev = np.inf
    for p in np.linspace(0, 1, 101):
        ev = ev_release(p, 100_000, CFG)
        assert ev < prev
        prev = ev


def test_higher_p_bad_never_increases_release_propensity():
    """At fixed amount, raising p_bad may only move a case away from
    OVERTURN."""
    for amount in (500, 5_000, 50_000, 500_000, 5_000_000):
        released_at = [decide(p, Ev(amount), CFG).released
                       for p in np.linspace(0, 1, 201)]
        # once it stops releasing it must never start again
        if True in released_at:
            last = len(released_at) - 1 - released_at[::-1].index(True)
            assert all(released_at[:last + 1]), (
                f"release propensity is not monotone at amount {amount}")


def test_ev_ceiling_is_set_by_margin_not_by_cap():
    """EV > 0 requires p < m/(1+m), so cap above that is inert."""
    for m in (0.15, 0.25, 0.40):
        cfg = PolicyConfig(margin=m, cap=0.99)
        ceiling = m / (1 + m)
        big = Ev(10_000_000)          # large enough that the flat fee is noise
        assert decide(ceiling - 0.01, big, cfg).action is Action.OVERTURN
        assert decide(ceiling + 0.01, big, cfg).action is not Action.OVERTURN


# --- the insufficiency gate --------------------------------------------------
def test_no_confidence_level_can_release_a_thin_file():
    """No p_bad, however extreme, releases a one-order file."""
    thin = Ev(500_000, orders=1, tenure=120)
    for p in (0.0, 0.001, 0.01, 0.5, 0.99, 1.0):
        d = decide(p, thin, CFG)
        assert d.action is not Action.OVERTURN, f"released a 1-order file at p_bad={p}"
        assert not d.evidence_sufficient


def test_thin_file_below_uphold_floor_states_the_gate():
    thin = Ev(500_000, orders=1, tenure=120)
    for p in (0.0, 0.001, 0.01, 0.5):
        assert "insufficient_evidence" in decide(p, thin, CFG).reasons


def test_upholding_needs_no_exculpatory_evidence():
    """Confidently-bad closes the case even on a thin file. The gate guards
    releases only; a p_bad=1.0 case must not reach a step-up dialogue."""
    thin = Ev(500_000, orders=2, tenure=33)     # the Case-2 shape: 2 orders, 33 days
    d = decide(1.0, thin, CFG)
    assert d.action is Action.UPHOLD
    assert any("confidently_bad" in r for r in d.reasons)
    assert any("uphold_needs_no_exculpation" in r for r in d.reasons)
    assert not d.evidence_sufficient      # still reported


def test_gate_reasons_name_the_missing_evidence():
    ok, reasons = evidence_sufficient(Ev(1000, orders=1, tenure=5), CFG)
    assert not ok
    assert any("thin_network_file" in r for r in reasons)
    assert any("short_tenure" in r for r in reasons)


# --- amount sensitivity ------------------------------------------------------
def test_same_p_bad_different_amounts_different_actions():
    """Amount enters the EV term, so it changes the action."""
    p = 0.35   # ambiguous: above the EV ceiling, below the uphold floor
    small = decide(p, Ev(500), CFG)
    large = decide(p, Ev(500_000), CFG)
    assert small.action is Action.UPHOLD
    assert large.action is Action.STEP_UP


def test_thin_file_routing_respects_the_stepup_floor():
    cfg = PolicyConfig(stepup_floor_inr=5_000)
    assert decide(0.1, Ev(50_000, orders=0, tenure=1), cfg).action is Action.STEP_UP
    assert decide(0.1, Ev(100, orders=0, tenure=1), cfg).action is cfg.insufficient_small_action


def test_confidently_bad_closes_the_case():
    d = decide(0.95, Ev(500_000), CFG)
    assert d.action is Action.UPHOLD
    assert any("confidently_bad" in r for r in d.reasons)


# --- every decision explains itself -----------------------------------------
def test_every_decision_carries_reasons():
    for p in (0.01, 0.15, 0.35, 0.75):
        for amount in (100, 10_000, 1_000_000):
            for orders in (0, 2, 40):
                d = decide(p, Ev(amount, orders=orders), CFG)
                assert d.reasons
                assert isinstance(d.action, Action)


def test_only_overturn_moves_money():
    assert Action.OVERTURN.releases_money
    for a in (Action.UPHOLD, Action.STEP_UP, Action.ESCALATE):
        assert not a.releases_money
