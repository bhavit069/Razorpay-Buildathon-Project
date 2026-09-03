"""The recovery ladder.

Most of these are structural: an outreach model built on five asserted
conversion rates can be made to say anything, so what is worth testing is not
the rates but the things that must hold whatever the rates turn out to be.

The exception, and the reason this file matters, is the first group. The
channel parameters are not free - METRICS.md 11 already pinned two points on
the decay curve when it published the 0.70/0.35 recontact band, and this module
has to reproduce them. If it does not, one of the two documents is wrong.
"""
import math

import pytest

from core import recovery as R
from core.metrics import RECONTACT_RANGE
from core.policy import Action


# --- (a) agreement with the frozen band -------------------------------------

def test_half_life_is_the_one_the_frozen_band_implies():
    """0.70 at ~2 min and 0.35 at ~1440 min. Exactly one exponential does both."""
    top, _, bottom = RECONTACT_RANGE
    implied = -math.log(2) * (1440 - 2) / math.log(bottom / top)
    assert abs(R.RecoveryConfig().half_life_min - implied) < 5, (
        f"the band implies a {implied:.0f} min half-life, module uses "
        f"{R.RecoveryConfig().half_life_min:.0f}")


def test_sms_alone_reproduces_the_in_session_anchor(ledger):
    """METRICS calls 0.70 'an in-session retry prompt'. That is this channel."""
    o = R.grade(ledger, only="sms")
    assert abs(o.blended_rate - RECONTACT_RANGE[0]) < 0.02, o.blended_rate


def test_email_alone_reproduces_the_next_day_anchor(ledger):
    """METRICS calls 0.35 'a next-day email'. Same."""
    o = R.grade(ledger, only="email")
    assert abs(o.blended_rate - RECONTACT_RANGE[2]) < 0.02, o.blended_rate


def test_the_ladder_beats_every_single_touch(ledger):
    """Otherwise there is no reason to build one."""
    full = R.grade(ledger).blended_rate
    for k in ("sms", "email", "voice", "human"):
        assert full > R.grade(ledger, only=k).blended_rate, k


def test_auto_is_deployment_inline():
    """Release in session is the inline deployment: everyone converts, free."""
    c = R.BY_KEY["auto"]
    assert (c.p_raw, c.cost_inr, c.latency_min) == (1.0, 0.0, 0.0)


# --- (b) constraints that hold whatever the rates are ------------------------

def test_step_up_never_gets_a_one_way_channel():
    """A step-up is an unanswered question. An SMS cannot discharge one."""
    for amount in (5_000, 50_000, 500_000, 5_000_000):
        p = R.plan(Action.STEP_UP, ev_release_inr=amount * 0.2, amount_inr=amount,
                   orders_prior=40, tenure_days=900)
        assert p.rungs, amount
        for rung in p.rungs:
            assert R.BY_KEY[rung.channel].two_way, (amount, rung.channel)


def test_escalate_goes_to_a_person():
    p = R.plan(Action.ESCALATE, 90_000, 400_000, 0, 0)
    assert [r.channel for r in p.rungs] == ["human"]


def test_uphold_recovers_nothing():
    p = R.plan(Action.UPHOLD, 90_000, 400_000, 40, 900)
    assert not p.contacted and p.ev_inr == 0.0 and p.cost_expected_inr == 0.0


def test_no_discretionary_rung_is_ever_added_at_a_loss():
    """A rung the ladder chose has to pay. A rung the policy mandated does not
    - an escalation gets a reviewer whether or not the arithmetic likes it,
    which is the whole point of escalating - and those are flagged rather than
    quietly averaged in with the rest."""
    for amount in (100, 1_200, 5_000, 90_000, 2_000_000):
        for act in (Action.OVERTURN, Action.STEP_UP, Action.ESCALATE):
            for rung in R.plan(act, amount * 0.2, amount, 40, 900).rungs:
                if rung.committed:
                    continue
                assert rung.ev_inr > 0, (act, amount, rung)


def test_a_committed_action_is_always_actioned():
    """An escalation whose EV of release is negative still gets a person. The
    case was escalated because ev_release is not to be trusted on it, so using
    ev_release to decide whether to bother is circular - and it silently
    dropped three real holdout escalations before this was caught."""
    for act in (Action.ESCALATE, Action.STEP_UP):
        p = R.plan(act, ev_release_inr=-40_000, amount_inr=8_000,
                   orders_prior=0, tenure_days=0)
        assert p.contacted, act
        assert p.rungs[0].committed
        assert any("committed_" in r for r in p.reasons)


def test_an_overturn_never_needs_the_committed_floor():
    """core.policy only issues OVERTURN when ev_release > 0, so the ladder can
    decide whether to contact honestly there and never falls back."""
    for amount in (100, 1_200, 90_000, 2_000_000):
        for rung in R.plan(Action.OVERTURN, amount * 0.2, amount, 40, 900).rungs:
            assert not rung.committed, (amount, rung)


def test_a_worthless_conversion_is_not_chased():
    """Negative EV of release means releasing loses money, so does chasing it."""
    p = R.plan(Action.OVERTURN, ev_release_inr=-5_000, amount_inr=20_000,
               orders_prior=40, tenure_days=900)
    assert not p.contacted
    assert any("worth_nothing" in r for r in p.reasons)


def test_the_ladder_is_monotone_in_value():
    """A bigger case never earns a shorter ladder or a cheaper best channel."""
    prev_rungs, prev_cost = 0, -1.0
    for amount in (500, 2_000, 10_000, 60_000, 300_000, 3_000_000):
        p = R.plan(Action.OVERTURN, amount * 0.2, amount, 40, 900)
        best = max((R.BY_KEY[r.channel].cost_inr for r in p.rungs), default=0.0)
        assert len(p.rungs) >= prev_rungs, amount
        assert best >= prev_cost, amount
        prev_rungs, prev_cost = len(p.rungs), best


def test_severity_actually_separates():
    """The point of the whole module: a small order and a large one are not
    handled the same way, and nobody wrote a severity table to make that so."""
    small = R.plan(Action.OVERTURN, 1_195 * 0.2, 1_195, 40, 900)
    large = R.plan(Action.OVERTURN, 515_681 * 0.2, 515_681, 75, 1534)
    assert "human" not in [r.channel for r in small.rungs]
    assert "human" in [r.channel for r in large.rungs]


def test_a_cheap_step_up_calls_and_an_expensive_one_uses_a_person():
    """The crossover in the docs, exercised on either side of itself."""
    cfg = R.RecoveryConfig()
    x = R.crossover_value(R.BY_KEY["human"], R.BY_KEY["voice"], cfg)
    assert 0 < x < float("inf")
    below = R.plan(Action.STEP_UP, x * 0.5, 60_000, 40, 900, cfg=cfg)
    above = R.plan(Action.STEP_UP, x * 2.0, 60_000, 40, 900, cfg=cfg)
    assert below.first == "voice"
    assert above.first == "human"


# --- (c) the arithmetic ------------------------------------------------------

def test_crossover_equalises_expected_value():
    cfg = R.RecoveryConfig()
    for a in R.CHANNELS:
        for b in R.CHANNELS:
            v = R.crossover_value(a, b, cfg)
            if v == float("inf") or v <= 0:
                continue
            ea = R.p_return(a, 0, cfg) * v - a.cost_inr
            eb = R.p_return(b, 0, cfg) * v - b.cost_inr
            assert abs(ea - eb) < 1e-6, (a.key, b.key, ea, eb)


def test_a_dominated_channel_never_crosses_over():
    """Cheaper and better converting means the dearer one never wins on
    conversion, and inf is the honest answer rather than a large number."""
    sms, human = R.BY_KEY["sms"], R.BY_KEY["human"]
    assert R.p_return(sms, 0) > R.p_return(human, 0) and sms.cost_inr < human.cost_inr
    assert R.crossover_value(human, sms, R.RecoveryConfig()) == float("inf")


@pytest.mark.parametrize("t", [0, 1, 30, 240, 1440, 10_000])
def test_probabilities_stay_probabilities(t):
    cfg = R.RecoveryConfig()
    for c in R.CHANNELS:
        for rung in range(4):
            assert 0.0 <= R.p_return(c, t, cfg, rung) <= 1.0


def test_decay_only_falls():
    cfg = R.RecoveryConfig()
    xs = [R.decay(t, cfg) for t in range(0, 3000, 60)]
    assert xs == sorted(xs, reverse=True)
    assert abs(R.decay(cfg.half_life_min, cfg) - 0.5) < 1e-9


def test_waiting_costs_money():
    """Time to recover is not decoration: the same case is worth less later."""
    now = R.plan(Action.OVERTURN, 100_000, 500_000, 40, 900, elapsed_min=0)
    later = R.plan(Action.OVERTURN, 100_000, 500_000, 40, 900, elapsed_min=2880)
    assert later.p_total < now.p_total
    assert later.ev_inr < now.ev_inr


# --- (d) the declared extras -------------------------------------------------

def test_ltv_is_off_by_default_and_only_ever_adds():
    off = R.plan(Action.OVERTURN, 50_000, 200_000, 214, 1680)
    on = R.plan(Action.OVERTURN, 50_000, 200_000, 214, 1680,
                cfg=R.RecoveryConfig(count_ltv=True))
    assert off.value_inr == 50_000
    assert on.value_inr > off.value_inr


def test_ltv_refuses_to_extrapolate_off_a_thin_file():
    cfg = R.RecoveryConfig(count_ltv=True)
    assert R.ltv_at_risk(200_000, orders_prior=2, tenure_days=900, cfg=cfg) == 0.0
    assert R.ltv_at_risk(200_000, orders_prior=40, tenure_days=30, cfg=cfg) == 0.0


def test_correlation_haircut_only_shrinks_later_rungs():
    cfg0 = R.RecoveryConfig(rung_correlation=0.0)
    cfg1 = R.RecoveryConfig(rung_correlation=0.8)
    c = R.BY_KEY["email"]
    assert R.p_return(c, 0, cfg0, rung=0) == R.p_return(c, 0, cfg1, rung=0)
    assert R.p_return(c, 0, cfg1, rung=2) < R.p_return(c, 0, cfg0, rung=2)


def test_optimism_is_bounded_by_the_declared_direction(ledger):
    """Both declared knobs are set pessimistically. Relaxing either may only
    improve the result, so the shipped figure is a floor, not a midpoint.

    Measured on total expected returns, not the blended rate. The rate is an
    average and it goes DOWN when the ladder reaches further into marginal
    cases, which is exactly what turning LTV on makes it do - 1144 cases
    contacted becomes 1372, returns rise from 905 to 1054, and the average of
    the larger set is lower. Asserting on the rate here would have called a
    genuine improvement a regression.
    """
    base = R.grade(ledger).expected_returns
    assert R.grade(ledger, R.RecoveryConfig(rung_correlation=0.0)).expected_returns >= base
    assert R.grade(ledger, R.RecoveryConfig(count_ltv=True)).expected_returns >= base


def test_every_case_the_system_did_not_uphold_is_actioned(ledger):
    """Falls out of the committed-action fix and is the property worth having:
    an overturn always has positive EV to chase, and a step-up or an escalation
    is contacted because the policy already said so. So the set left alone is
    exactly the upheld blocks, and nothing can go quietly missing between the
    decision and the outreach."""
    o = R.grade(ledger)
    upheld = sum(1 for r in ledger.records if r.action == "UPHOLD")
    assert o.uncontacted == upheld
    assert o.contacted == o.cases - upheld


# --- (e) capacity ------------------------------------------------------------

def test_human_capacity_is_rationed_by_value(ledger):
    """A ladder with unlimited human callbacks rebuilds the same fantasy the
    reviewer baseline in METRICS.md 2 exists to puncture."""
    tight = R.grade(ledger, R.RecoveryConfig(human_calls_per_day=2))
    assert tight.human_denied > 0
    loose = R.grade(ledger, R.RecoveryConfig(human_calls_per_day=10_000))
    assert loose.human_denied == 0
    assert tight.spend_inr < loose.spend_inr


def test_denied_overturns_fall_to_the_next_channel_rather_than_off_the_list(ledger):
    tight = R.grade(ledger, R.RecoveryConfig(human_calls_per_day=1))
    loose = R.grade(ledger, R.RecoveryConfig(human_calls_per_day=10_000))
    # everything except an escalation has somewhere cheaper to fall to
    assert tight.contacted == loose.contacted - tight.stranded
    assert tight.blended_rate < loose.blended_rate


def test_a_denied_escalation_is_stranded_and_counted(ledger):
    """An escalation's only permitted channel is a person. Run out of people
    and the case is not handled more cheaply, it is not handled - which is a
    different thing from the cases the ladder chose to leave alone, and is
    counted separately so it cannot hide in the same number."""
    tight = R.grade(ledger, R.RecoveryConfig(human_calls_per_day=1))
    assert tight.stranded > 0
    assert R.grade(ledger).stranded == 0


def test_capacity_does_not_bind_at_this_volume(ledger):
    """Reported rather than assumed away: at 29 blocked orders a day the
    default budget is not the constraint, and the test says so out loud."""
    o = R.grade(ledger)
    assert o.human_denied == 0
    assert o.human_minutes / 60 / 61 < 3.0     # agent-hours per day


# --- (f) determinism ---------------------------------------------------------

def test_planning_is_deterministic():
    args = (Action.OVERTURN, 90_000, 400_000, 47, 1453)
    a, b = R.plan(*args), R.plan(*args)
    assert a == b


def test_grading_does_not_flip_coins(ledger):
    assert R.grade(ledger) == R.grade(ledger)
