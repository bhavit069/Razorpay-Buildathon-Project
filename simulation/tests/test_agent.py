"""Agent invariants. ARCHITECTURE.md 2.

Phase 2 claims the model explains and negotiates but never decides whether
money moves. These check that.
"""
import ast
import json
import os

import pytest

from agent.ledger import BrokenChain, Ledger, link
from agent.llm import CacheMiss, LLMClient
from agent.orchestrator import Orchestrator
from agent.stepup import StepUpFacts
from agent.tools import TOOL_NAMES, Toolbox
from agent.verdict import allowed_numbers, check_citations, write
from core.policy import Action, PolicyConfig, decide

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = PolicyConfig(cap=0.20)      # the EV point, same as the report and demo


@pytest.fixture(scope="module")
def orc(store, model, vault):
    personas = {t.payment_id: (t.persona, t.true_outcome)
                for t in vault.grade(store.payment_ids(store.split("holdout")[:60]))}
    return Orchestrator(store, model, CFG, LLMClient("offline"), personas=personas)


@pytest.fixture(scope="module")
def results(orc, store):
    return orc.run(store.split("holdout"), limit=40)


# --- the LLM cannot decide ---------------------------------------------------
def test_no_llm_module_imports_the_policy_decision(store):
    """Only tools.py and the orchestrator may call decide()."""
    for rel in ("agent/verdict.py", "agent/stepup.py"):
        tree = ast.parse(open(os.path.join(ROOT, rel), encoding="utf-8").read())
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "decide" not in called, f"{rel} calls policy.decide directly"


def test_stepup_facts_cannot_name_an_action():
    """The extraction schema has no field for a decision."""
    fields = set(vars(StepUpFacts()))
    for a in Action:
        assert a.value.lower() not in fields
    assert not {"action", "decision", "release", "recommendation"} & fields


def test_verification_never_touches_the_record(store, model):
    """A verified customer must score what they scored before answering."""
    for ev in store.split("holdout")[:30]:
        before = model.predict_one(ev)
        v = StepUpFacts(address_confirmed=True, prior_order_confirmed=True,
                        prepaid_accepted=True).as_verification()
        d = decide(before, ev, CFG, v)
        assert d.p_bad == before, "verification moved p_bad; it must not"


def test_evidence_channel_stays_closed(store):
    """Nothing may be written into the feature blocks from outside."""
    ev = store.split("holdout")[0]
    with pytest.raises(KeyError):
        ev.merged({"action": "OVERTURN"})
    with pytest.raises(KeyError):
        ev.merged({"risk_score": 0.0})


def test_evasion_clears_nothing():
    assert not StepUpFacts(evaded=True, address_confirmed=True,
                           prior_order_confirmed=True).as_verification().clears_gate
    assert not StepUpFacts().as_verification().clears_gate


def test_one_confirmation_is_not_enough():
    assert not StepUpFacts(address_confirmed=True).as_verification().clears_gate
    assert StepUpFacts(address_confirmed=True,
                       prior_order_confirmed=True).as_verification().clears_gate


def test_verification_cannot_release_a_bad_case(store, model):
    """Clearing the gate is not a release: a high p_bad still upholds."""
    thin = [e for e in store.split("holdout")
            if e.network["network_orders_prior"] < 3][:20]
    v = StepUpFacts(address_confirmed=True, prior_order_confirmed=True,
                    prepaid_accepted=True).as_verification()
    assert v.clears_gate
    for ev in thin:
        assert decide(0.95, ev, CFG, v).action is Action.UPHOLD


# --- verdicts are audited ----------------------------------------------------
def test_invented_numbers_are_caught(store, model):
    ev = store.split("holdout")[0]
    d = decide(model.predict_one(ev), ev, CFG)
    ok, bad = check_citations(
        "Released. The customer has 999999 prior orders over 424242 days.", ev, d)
    assert not ok
    assert "999999" in bad and "424242" in bad


def test_real_numbers_pass(store, model):
    ev = store.split("holdout")[0]
    d = decide(model.predict_one(ev), ev, CFG)
    text = (f"Released. The file shows {ev.network['network_orders_prior']:.0f} "
            f"prior orders over {ev.network['network_tenure_days']:.0f} days. "
            f"p_bad {d.p_bad:.3f}.")
    ok, bad = check_citations(text, ev, d)
    assert ok, f"rejected legitimate figures: {bad}"


def test_every_generated_verdict_is_audit_clean(results):
    dirty = [r.payment_id for r in results if not r.citations_ok]
    assert not dirty, f"verdicts with unsupported figures: {dirty}"


def test_verdict_states_the_action(results):
    words = {"OVERTURN": ("released",), "UPHOLD": ("upheld",),
             "STEP_UP": ("verification", "held"), "ESCALATE": ("reviewer", "referred")}
    for r in results:
        low = r.verdict.lower()
        assert any(w in low for w in words[r.action]), \
            f"{r.payment_id} verdict does not state {r.action}: {r.verdict[:80]}"


def test_template_fallback_is_labelled(results):
    """Offline output must not claim to be model output."""
    for r in results:
        assert r.verdict_source in ("claude", "cache", "template")
    assert all(r.verdict_source == "template" for r in results), \
        "expected offline mode with no cache present"


# --- ledger ------------------------------------------------------------------
def test_ledger_chain_verifies(orc, results):
    assert len(orc.ledger) == len(results)
    orc.ledger.verify()


def test_tampering_breaks_the_chain(tmp_path):
    led = Ledger(str(tmp_path / "l.jsonl"))
    for i in range(5):
        led.append({"payment_id": f"pay_{i}", "action": "UPHOLD"})
    led.verify()
    led.entries[2].payload["action"] = "OVERTURN"      # rewrite history
    with pytest.raises(BrokenChain) as e:
        led.verify()
    assert "entry 2" in str(e.value)


def test_ledger_reloads_from_disk(tmp_path):
    p = str(tmp_path / "l.jsonl")
    a = Ledger(p)
    for i in range(3):
        a.append({"payment_id": f"pay_{i}"})
    b = Ledger(p)
    assert len(b) == 3
    b.verify()
    assert b.head == a.head


def test_every_case_is_in_the_ledger(orc, results):
    for r in results:
        e = orc.ledger.find(r.payment_id)
        assert e is not None
        assert e.payload["action"] == r.action


# --- tool discipline ---------------------------------------------------------
def test_every_case_ran_the_same_tool_spine(results):
    for r in results:
        names = [t["tool"] for t in r.tool_trace]
        assert names[0] == "adjudicate"
        assert names[1] == "policy_decide"
        assert "record_verdict" in names


def test_stepped_up_cases_readjudicate_through_the_same_tools(results):
    stepped = [r for r in results if r.stepped_up]
    if not stepped:
        pytest.skip("no step-ups in this slice")
    for r in stepped:
        names = [t["tool"] for t in r.tool_trace]
        # A cleared step-up re-runs the policy, not the model.
        f = r.facts
        cleared = (f and not f.get("evaded")
                   and (f.get("address_confirmed") or f.get("prior_order_confirmed"))
                   and sum(bool(f.get(k)) for k in ("address_confirmed",
                                                    "prior_order_confirmed",
                                                    "prepaid_accepted")) >= 2)
        if cleared:
            assert names.count("policy_decide") == 2
        assert names.count("adjudicate") == 1,             "step-up recomputed p_bad; verification is not evidence"


def test_tool_names_match_the_toolbox(store, model):
    tb = Toolbox(store, model, CFG)
    for n in TOOL_NAMES:
        assert hasattr(tb, n)


def test_no_tool_accepts_free_text(store, model):
    """No signature through which generated prose could reach the policy."""
    import inspect
    tb = Toolbox(store, model, CFG)
    for name in ("adjudicate", "policy_decide"):
        params = inspect.signature(getattr(tb, name)).parameters
        assert "text" not in params and "verdict" not in params


# --- replay ------------------------------------------------------------------
def test_replay_mode_refuses_to_invent(tmp_path, store, model):
    """The demo dry run must fail loudly on a cache miss rather than quietly
    falling back to a template."""
    llm = LLMClient("replay", cache_dir=str(tmp_path))
    ev = store.split("holdout")[0]
    d = decide(model.predict_one(ev), ev, CFG)
    with pytest.raises(CacheMiss):
        write(ev, d, llm)


def test_cache_round_trip(tmp_path):
    llm = LLMClient("offline", cache_dir=str(tmp_path))
    c1 = llm.complete("sys", [{"role": "user", "content": "hi"}],
                      template=lambda: "templated")
    assert c1.source == "template" and not c1.from_model
    llm._write_cache(c1.key, {"text": "recorded", "usage": {}})
    c2 = llm.complete("sys", [{"role": "user", "content": "hi"}],
                      template=lambda: "templated")
    assert c2.source == "cache" and c2.text == "recorded" and c2.from_model


def test_orchestrator_is_deterministic(store, model, vault):
    a = Orchestrator(store, model, CFG, LLMClient("offline")).run(
        store.split("holdout"), limit=15)
    b = Orchestrator(store, model, CFG, LLMClient("offline")).run(
        store.split("holdout"), limit=15)
    assert [(r.payment_id, r.action, round(r.p_bad, 9)) for r in a] == \
           [(r.payment_id, r.action, round(r.p_bad, 9)) for r in b]
    assert [r.verdict for r in a] == [r.verdict for r in b]


# --- the agent is a delivery mechanism, not a second decision system ---------
def test_agent_reproduces_the_backtest(store, model, vault):
    """Wrapping the decision system in an agent must not change what it
    decides. Same action and identical p_bad, except where a step-up produced
    verification the backtest could not run."""
    from core.backtest import run as backtest_run

    bt = {r.payment_id: r for r in backtest_run(store, model, CFG, "holdout").records}
    orc = Orchestrator(store, model, CFG, LLMClient("offline"))
    agent = orc.run(store.split("holdout"), limit=250)

    for r in agent:
        assert r.p_bad == bt[r.payment_id].p_bad, (
            f"{r.payment_id}: agent scored {r.p_bad}, backtest {bt[r.payment_id].p_bad}")

    diverged = [r for r in agent if r.action != bt[r.payment_id].action]
    for r in diverged:
        assert r.stepped_up, (
            f"{r.payment_id} diverged from the backtest without a step-up: "
            f"{bt[r.payment_id].action} -> {r.action}")
        assert bt[r.payment_id].action == Action.STEP_UP.value


def test_agent_run_prices_the_same_way(store, model, vault):
    """An agent run must be gradeable by the Phase 1 metrics code."""
    from core.metrics import grade

    orc = Orchestrator(store, model, CFG, LLMClient("offline"))
    res = orc.run(store.split("holdout"), limit=120)
    o = grade(orc.as_core_ledger(res), vault, None)
    assert o.n_cases == len(res)
    assert o.n_released == sum(r.action == "OVERTURN" for r in res)
    assert 0.0 <= o.precision <= 1.0
    assert o.recovered_inr >= 0 and o.fraud_admitted_inr >= 0


# --- escalation briefs -------------------------------------------------------
def test_escalations_carry_a_brief(store, model):
    orc = Orchestrator(store, model, CFG, LLMClient("offline"))
    res = orc.run(store.split("holdout"), limit=300)
    esc = [r for r in res if r.action == "ESCALATE"]
    if not esc:
        pytest.skip("no escalations in this slice")
    for r in esc:
        assert r.brief, f"{r.payment_id} escalated with no brief"
        assert len(r.brief) > 200
        assert "missing" in r.brief.lower()
    for r in res:
        if r.action != "ESCALATE":
            assert not r.brief, "brief written for a case that was not escalated"


def test_brief_is_citation_checked(store, model):
    from agent.verdict import write_brief
    ev = next(e for e in store.split("holdout")
              if e.network["network_orders_prior"] < 3)
    d = decide(model.predict_one(ev), ev, CFG)
    b = write_brief(ev, d, LLMClient("offline"))
    assert b.citations_ok
    ok, bad = check_citations(b.text, ev, d)
    assert ok, f"brief cites unsupported figures: {bad}"


def test_latency_is_reported(store, model):
    orc = Orchestrator(store, model, CFG, LLMClient("offline"))
    res = orc.run(store.split("holdout"), limit=50)
    lat = orc.summary(res)["latency_ms"]
    assert set(lat) == {"p50", "p95", "max"}
    assert lat["p50"] <= lat["p95"] <= lat["max"]
