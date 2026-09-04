"""Two runs, same seed, byte-identical ledgers. ARCHITECTURE.md 1.4/1.6."""
import numpy as np

from core.backtest import run
from core.metrics import StepUpModel, apply_per_merchant, grade
from core.policy import Action, PolicyConfig

CFG = PolicyConfig(cap=0.10)


def test_two_replays_are_byte_identical(store, model):
    a = run(store, model, CFG, "holdout").to_jsonl()
    b = run(store, model, CFG, "holdout").to_jsonl()
    assert a == b


def test_replay_is_chronological(store, model):
    led = run(store, model, CFG, "holdout")
    ts = [r.created_at for r in led.records]
    assert ts == sorted(ts)
    assert [r.seq for r in led.records] == list(range(len(led)))


def test_redecide_at_same_config_is_identity(store, model):
    led = run(store, model, CFG, "holdout")
    again = led.redecide(CFG)
    assert led.to_jsonl() == again.to_jsonl()


def test_redecide_leaves_the_model_alone(store, model):
    """Moving the operating point must not move any probability."""
    led = run(store, model, CFG, "holdout")
    for cap in (0.01, 0.05, 0.20):
        moved = led.redecide(CFG.for_merchant(cap))
        assert np.array_equal(led.p_bad(), moved.p_bad())
        assert led.payment_ids() == moved.payment_ids()


def test_tightening_the_cap_never_releases_more(store, model):
    led = run(store, model, CFG, "holdout")
    counts = [led.redecide(CFG.for_merchant(c)).released().sum()
              for c in (0.005, 0.01, 0.02, 0.05, 0.10, 0.20)]
    assert counts == sorted(counts), f"release count not monotone in cap: {counts}"


def test_grading_is_deterministic(store, model, vault):
    led = run(store, model, CFG, "holdout")
    su = StepUpModel()
    a = grade(led, vault, su)
    b = grade(led, vault, su)
    assert a.net_contribution_inr == b.net_contribution_inr
    assert a.n_released == b.n_released
    assert a.n_stepup_passed == b.n_stepup_passed


def test_stepup_resolution_respects_its_parameters(store, model, vault):
    """A more generous verification assumption releases at least as many."""
    led = run(store, model, CFG, "holdout")
    lo = grade(led, vault, StepUpModel(0.85, 0.03))
    hi = grade(led, vault, StepUpModel(0.95, 0.03))
    assert hi.n_stepup_passed >= lo.n_stepup_passed


def test_per_merchant_application_preserves_every_case(store, model, vault):
    led = run(store, model, CFG, "holdout")
    caps = {m: 0.05 for m in {r.merchant for r in led.records}}
    pm = apply_per_merchant(led, caps, CFG)
    assert len(pm) == len(led)
    assert sorted(pm.payment_ids()) == sorted(led.payment_ids())
    assert [r.seq for r in pm.records] == sorted(r.seq for r in pm.records)


def test_ledger_records_are_self_describing(store, model):
    """redecide() re-runs the gate without a side table."""
    led = run(store, model, CFG, "holdout")
    r = led.records[0]
    for field in ("network_orders_prior", "network_tenure_days", "p_bad",
                  "amount_inr", "merchant", "block_reason", "reasons"):
        assert hasattr(r, field)


def test_actions_are_exhaustive(store, model):
    led = run(store, model, CFG, "holdout")
    assert set(led.counts()) == {a.value for a in Action}
    assert sum(led.counts().values()) == len(led)
