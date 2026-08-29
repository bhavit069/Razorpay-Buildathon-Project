"""The queue haircut and the rationed reviewer.

Both were added after review, and both are the kind of arithmetic that is easy
to get wrong in a way nobody notices until it is on a slide. The queue discount
in particular has to be applied exactly once.
"""
import numpy as np
import pytest

from core.backtest import run
from core.metrics import (HUMAN_ACCURACY, HUMAN_COVERAGE, RECONTACT_RANGE,
                          REVIEW_COST_INR, Deployment, StepUpModel,
                          baseline_human_rationed, baseline_human_reviewer,
                          grade, recontact_arithmetic)
from core.policy import Action, PolicyConfig

CAP = 0.20


@pytest.fixture(scope="module")
def ledger(store, model):
    return run(store, model, PolicyConfig(cap=CAP), "holdout")


@pytest.fixture(scope="module")
def stepup():
    return StepUpModel()


def test_recontact_discount_is_applied_once(ledger, vault, stepup):
    """Net contribution must be margin on the already-discounted recovery, not
    a second haircut on top of it."""
    inline = grade(ledger, vault, stepup, deployment=Deployment.inline())
    for rate in RECONTACT_RANGE:
        q = grade(ledger, vault, stepup, deployment=Deployment.queue(rate))
        assert q.recovered_inr == pytest.approx(inline.recovered_inr * rate)
        fixed = inline.net_contribution_inr - ledger.config.margin * inline.recovered_inr
        expected = ledger.config.margin * inline.recovered_inr * rate + fixed
        assert q.net_contribution_inr == pytest.approx(expected)


def test_fraud_admitted_does_not_move_with_recontact(ledger, vault, stepup):
    inline = grade(ledger, vault, stepup, deployment=Deployment.inline())
    for rate in RECONTACT_RANGE:
        q = grade(ledger, vault, stepup, deployment=Deployment.queue(rate))
        assert q.fraud_admitted_inr == inline.fraud_admitted_inr
        assert q.n_released == inline.n_released


def test_published_arithmetic_reproduces_grade(ledger, vault, stepup):
    """Every row of the table in METRICS.md 11 has to come out of grade()."""
    ra = recontact_arithmetic(ledger, vault, stepup)
    for row in ra["rows"]:
        dep = (Deployment.inline() if row["rate"] == 1.0
               else Deployment.queue(row["rate"]))
        g = grade(ledger, vault, stepup, deployment=dep)
        assert row["net_contribution_inr"] == pytest.approx(g.net_contribution_inr)


def test_breakeven_rate_is_where_contribution_is_zero(ledger, vault, stepup):
    ra = recontact_arithmetic(ledger, vault, stepup)
    r = ra["breakeven_rate"]
    assert 0.0 < r < 1.0
    g = grade(ledger, vault, stepup, deployment=Deployment.queue(r))
    assert g.net_contribution_inr == pytest.approx(0.0, abs=1.0)


def test_drag_is_fixed_across_rates(ledger, vault, stepup):
    ra = recontact_arithmetic(ledger, vault, stepup)
    assert len({row["drag_inr"] for row in ra["rows"]}) == 1


def test_rationed_reviewer_reaches_only_the_top_slice(ledger, vault):
    n = len(ledger)
    for cov in HUMAN_COVERAGE:
        h = baseline_human_rationed(ledger, vault, cov)
        assert h.counts["reviewed"] == round(cov * n)
        assert h.n_released <= h.counts["reviewed"]


def test_unreviewed_cases_recover_nothing(ledger, vault):
    """Below the line the block stands. Nobody undid it."""
    amounts = ledger.amounts()
    cov = HUMAN_COVERAGE[0]
    h = baseline_human_rationed(ledger, vault, cov)
    cutoff = np.sort(amounts)[::-1][h.counts["reviewed"] - 1]
    assert amounts[h.released_mask].min() >= cutoff


def test_more_coverage_never_recovers_less(ledger, vault):
    prev = baseline_human_rationed(ledger, vault, HUMAN_COVERAGE[0])
    for cov in HUMAN_COVERAGE[1:]:
        h = baseline_human_rationed(ledger, vault, cov)
        assert h.recovered_inr >= prev.recovered_inr
        prev = h


def test_rationed_charges_only_for_cases_reviewed(ledger, vault):
    for cov in HUMAN_COVERAGE:
        h = baseline_human_rationed(ledger, vault, cov)
        assert h.review_cost_inr == pytest.approx(REVIEW_COST_INR * h.counts["reviewed"])


def test_full_coverage_rationed_matches_full_reviewer(ledger, vault):
    """The rationed reviewer at 100% coverage is the same person as the
    unrationed one, so the two must agree."""
    full = baseline_human_reviewer(ledger, vault, HUMAN_ACCURACY, seed=11)
    rat = baseline_human_rationed(ledger, vault, 1.0, HUMAN_ACCURACY, seed=11)
    assert rat.n_released == full.n_released
    assert rat.recovered_inr == pytest.approx(full.recovered_inr)
    assert rat.net_contribution_inr == pytest.approx(full.net_contribution_inr)


def test_coverage_beats_accuracy(ledger, vault, stepup, store, model):
    """The claim being made in METRICS.md 2: a reviewer wins case for case and
    loses on reach. If this ever flips, the narrative is wrong."""
    o = grade(ledger, vault, stepup)
    full = baseline_human_reviewer(ledger, vault)
    assert full.net_contribution_inr > o.net_contribution_inr
    for cov in HUMAN_COVERAGE:
        h = baseline_human_rationed(ledger, vault, cov)
        assert h.net_contribution_inr < o.net_contribution_inr
        assert h.recall_recoverable < o.recall_recoverable


# ---------------------------------------------------------------------------
# The demo screen
# ---------------------------------------------------------------------------
def test_case_room_contains_every_showcase_case(store, vault, model, data_dir):
    """Five of the six showcase cases sit past position 400 in chronological
    order. An earlier version took a plain prefix of the holdout, so the demo
    screen rendered one tagged case out of six and none of the recorded model
    verdicts were ever requested. Nothing threw and no test failed; the page
    just quietly had nothing on it worth showing.
    """
    from core.showcase import pick as pick_showcase
    from service.case_room import collect

    want = {e.payment_id for e, _, _ in pick_showcase(store, vault, model).values()}
    got = collect(data_dir, limit=10)
    ids = {c["payment_id"] for c in got}
    assert want <= ids, f"missing from the case room: {sorted(want - ids)}"
    # and they have to be at the top, not buried
    assert all(c["roles"] for c in got[:len(want)])


def test_recorded_model_verdicts_reach_the_demo(data_dir):
    """Whatever is in the replay cache should show up as model output on the
    screen. If this drops to zero the demo is all templates again."""
    from service.case_room import collect
    got = collect(data_dir, limit=10)
    assert any(c["verdict_source"] == "cache" for c in got)
