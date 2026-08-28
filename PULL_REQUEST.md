# Phases 1 and 2: decision core, agent, generated metrics

Target branch: `dev`
Scope: `ARCHITECTURE.md` 1.1 to 1.6 and 2.1 to 2.5. The service and UI (2.6) are not started.
Code snapshot: [`pr/simulation/`](simulation/)

## What this does

Reviews orders a merchant's fraud stack blocked, and decides which blocks were
wrong.

Two layers. Phase 1 is a deterministic decision system: evidence assembly, a
calibrated model, an expected-value policy, a chronological backtest, and a
metrics harness that regenerates `METRICS.md` from a seed. Phase 2 wraps it in
an agent that writes verdicts, runs verification exchanges, and keeps a
tamper-evident log.

The agent adds no decision logic. That is checked rather than asserted: see
"Agent reproduces the backtest" below.

Headline on a 1,775-case temporal holdout, cap 0.02: 98.6% overturn precision,
81.1% recall of recoverable revenue, Rs 7.04 cr recovered against Rs 22.57 L of
fraud admitted, Rs 1.53 cr net contribution, 17% abstention.

Read "How valid is this" before quoting any of it.

## Files

`development/core/`, 1,587 lines:

| Module | Does |
|---|---|
| `truth.py` | Only module that opens the answer key |
| `feature_store.py` | Evidence assembly and validation |
| `model.py` | LightGBM plus isotonic calibration |
| `policy.py` | EV rule over four actions |
| `backtest.py` | Chronological replay, self-describing ledger |
| `metrics.py` | Grading, bootstrap CIs, baselines, frontier |
| `report.py` | Writes `METRICS.md` and three figures |

`development/agent/`, 1,041 lines:

| Module | Lines | Does |
|---|---:|---|
| `ledger.py` | 89 | Append-only log, each entry hashing the previous |
| `llm.py` | 149 | Claude access with a disk replay cache |
| `tools.py` | 102 | The agent's only route to the model and policy |
| `verdict.py` | 301 | Case notes and escalation briefs, citation-checked |
| `stepup.py` | 227 | Verification exchange and fact extraction |
| `orchestrator.py` | 173 | The case loop |

`development/tests/`, 721 lines, 61 tests, 4s: leakage 9, policy 12,
determinism 10, agent 28.

Also `notebooks/` (five executable explainers, generated so outputs cannot
drift from code), `seed_check.py`, `demo.py`, `notes.txt`, `run.py`.

One change to pre-existing code: `datagen/generate.py` gains a `--customers`
flag. Default behaviour unchanged; the 100k baseline reproduces byte for byte.

## Agent reproduces the backtest

`ARCHITECTURE.md` says the decision system does not change between phases.
Tested on 300 holdout cases:

- `p_bad` identical on all 300, difference exactly 0.0
- 2 action divergences, both step-ups the backtest has no way to run

`test_agent_reproduces_the_backtest` asserts it, and `demo.py` rechecks it every
run. Agent decisions are also priced by `core.metrics.grade` unchanged, via
`as_core_ledger()`, so agent and backtest figures sit on the same footing rather
than being separate accountings.

## How valid is this

The numbers above look better than they are.

### The rupee figures are seed noise

`python run.py seeds` reruns the pipeline on five independently generated
worlds:

| seed | holdout | local AUC | +network AUC | lift | precision | net contribution |
|---|---|---|---|---|---|---|
| 42 | 1775 | 0.8682 | 0.9134 | +0.0452 | 98.6% | Rs 1.53 cr |
| 1 | 1745 | 0.8501 | 0.9209 | +0.0709 | 97.8% | Rs 1.87 cr |
| 2 | 1486 | 0.8414 | 0.9066 | +0.0651 | 98.4% | Rs 1.15 cr |
| 3 | 1826 | 0.8602 | 0.9321 | +0.0719 | 99.1% | Rs 1.80 cr |
| 4 | 1800 | 0.8678 | 0.9109 | +0.0431 | 98.5% | Rs 1.19 cr |

Net contribution ranges Rs 1.15 cr to Rs 1.87 cr, sd Rs 30 L. That spread is
wider than the bootstrap interval in `METRICS.md` 1, so the limit is how the
world was generated, not how many holdout cases there are.

Precision holds: 97.8% to 99.1%. AUC lift ranges +0.043 to +0.072, mean +0.059.
Seed 42, used throughout, is the least favourable of the five.

Quote precision as a number. Quote money as a range.

### Corrections made to earlier claims

**Confidence intervals were computed wrong.** 36% of holdout cases belong to a
customer appearing more than once, and those cases share a network file and a
device. `bootstrap_ci` now resamples customers. The correction was small, 0.95x
to 1.11x on interval width, but the old method was wrong on principle.

**"Holdout scored once" was not true.** It is read many times: headline, four
baselines, an 11-point frontier, per-merchant, three ablations, two sensitivity
grids, the failure exhibit. What is true is that no parameter or operating point
was chosen on it. `METRICS.md` now says that instead.

**The moat is smaller than `IDEA.md` claims, and an interim measurement of mine
overstated it.** `IDEA.md` 4.1 says +0.107 AUC; actual is +0.045 on seed 42,
+0.059 across five. Separately, a measurement during this work put the moat at
+63% of net recovery. That compared both models at a shared raw-probability
threshold, which measures calibration rather than evidence. With both calibrated
and each tuned off-holdout, it is +4.6% of net contribution.

What holds is about shape: network evidence releases 188 more orders at higher
precision at the same time. A threshold move trades those against each other.

### Value is concentrated in one merchant

Aurum Jewels is 451 of 1,775 holdout cases and 77% of holdout value. The rupee
headline is largely a measurement of one merchant's blocked pile.

### Remaining limits

- Ground truth is authored. Calibration and sweeps reduce circularity, not
  remove it.
- `network_clean_rate` carries 0.346 of gain at rank 1, against the ~0.49
  `DATA_CARD.md` 6.4 warns about. Confirmed, not resolved.
- Step-up pass rates are assumed, swept across a 3x3 grid.
- Fraudsters in this data do not adapt to the review system existing.
- Determinism verified on one machine. Cross-platform float behaviour untested.
- **No verdict has been through Claude.** No API key on this machine, so every
  verdict is a deterministic template tagged `source="template"`.

### What to trust

The mechanism, the direction of the results, and precision. The rupee magnitudes
show the accounting works; they are not a forecast.

## Design decisions

**Answer key behind one module.** `core/truth.py` is the only thing that opens
`ground_truth.jsonl.gz`. `training_labels()` raises `HoldoutPeek` on a non-train
id; `grade()` is unrestricted and used by `metrics.py`. `test_leakage.py`
AST-parses `feature_store.py`, `policy.py` and `backtest.py` and asserts none of
them import it.

**Ablation is an argument, not a fork.** `as_matrix(cases, blocks=("local",))`
against `("local","network")`, asserted by a test.

**Calibration.** The policy consumes probabilities as prices, so isotonic
regression is fitted on the last 20% of train, chronologically. Model fitted on
the first 80%, never refitted. Hyperparameters are fixed constants: with ~6.5k
rows a search would fit the calibration slice.

**Three-way temporal split.** Fit on train[:80%], calibrate on train[80%:],
choose operating points on that slice, report on holdout.

**Two rupee columns.** Net (gross convention) is recovered minus admitted, how
the industry and `IDEA.md` quote it. Net contribution is margin on recovered
sales minus the full basket plus overhead on bad releases. They diverge:
release-everything scores Rs 5.51 cr on the first and -Rs 8.79 L on the second.

**Step-up outcomes are parametric in the backtest.** The dataset cannot say
whether a customer would pass verification, so `backtest.py` records `STEP_UP`
unresolved and `metrics.py` resolves it against a declared grid. This also keeps
truth out of `backtest.py`.

**The model cannot decide.** `tools.py` is the only route to the policy and no
function there takes text. `StepUpFacts` has no field naming an action. Verdicts
receive a decision already made. All three are asserted by tests.

**Verification is not evidence.** A step-up confirms someone controls the
account; it does not create a payment record. It enters at the insufficiency
gate and leaves `p_bad` untouched. Two earlier versions got this wrong, see
Defects below.

## Results

1,775-case holdout, seed 42, each model at its own calibration-chosen cap.

| Policy | cap | Released | Precision | Recovered | Fraud admitted | Net | Net contribution |
|---|---|---|---|---|---|---|---|
| Do nothing | | 0 | n/a | Rs 0 | Rs 0 | Rs 0 | Rs 0 |
| Release everything | | 1,775 | 80.8% | Rs 7.43 cr | Rs 1.92 cr | Rs 5.51 cr | -Rs 8.79 L |
| Merchant-local only | 0.005 | 993 | 98.3% | Rs 6.68 cr | Rs 20.18 L | Rs 6.47 cr | Rs 1.47 cr |
| Network only | 0.050 | 802 | 98.1% | Rs 6.54 cr | Rs 20.67 L | Rs 6.34 cr | Rs 1.43 cr |
| Local + network | 0.020 | 1,181 | 98.6% | Rs 7.04 cr | Rs 22.57 L | Rs 6.82 cr | Rs 1.53 cr |

Release-everything recovers the most gross revenue and still loses money, which
is the argument for a policy rather than an amnesty.

Per-merchant caps give Rs 1.54 cr against Rs 1.31 cr for a single global cap,
+17%, by releasing fewer orders at higher precision.

| Evidence | AUC | AP | Brier |
|---|---|---|---|
| local + network | 0.9134 | 0.7633 | 0.0781 |
| local | 0.8682 | 0.6189 | 0.1026 |
| network | 0.7768 | 0.6498 | 0.0926 |

Agent run, 300 cases at cap 0.02: 151 released at 99.3% correct, 75 upheld, 73
held for verification, 1 escalated. All 300 verdicts citation-clean, ledger
verifies. Latency p50 0.78ms, p95 1.03ms, offline.

## Other findings

**The cap saturates at 0.20.** `EV > 0` requires `p < m/(1+m)` as `f/A` goes to
zero, independent of order size. At a 25% margin that is 0.20, so any cap above
it does nothing. `IDEA.md` 7 sweeps a threshold to 0.50, which an EV policy
cannot reach. Regenerate that table before using it.

**Five of six showcase cases are in the training split.** `IDEA.md` 5 narrates
six payment IDs. All six exist with matching outcomes, but only one is holdout.
`notebooks/04_worked_cases.ipynb` finds holdout substitutes. Two also no longer
behave as written: Case 3 abstains where the pitch says it overturns, Case 5
abstains rather than producing its Rs 8.4 L loss.

**LightGBM's default importance hid a known problem.** `DATA_CARD.md` 6.4 warns
that `network_clean_rate` dominates. The default `feature_importances_` is split
count, which is flat and ranks it sixth at 0.085. Gain puts it first at 0.346.
`model.importances()` now takes a `kind` argument and `METRICS.md` 4 prints both.

**`IDEA.md` and `DATA_CARD.md` carry stale numbers throughout.** Row counts,
block rates and the moat table come from an earlier generator. Audit in
`development/README.md`.

## Defects fixed

**`--n 300000` broke calibration.** `n_customers` was fixed at 12,000 and `--n`
never touched it, so regenerating at 300k tripled orders-per-customer from 8.3
to 25:

| | block rate | FP share | value ratio | holdout blocked |
|---|---|---|---|---|
| 100k baseline | 2.82% | 75.5% | 3.79x | 622 |
| 300k, customers fixed | 6.31% | 84.2% | 5.09x | 5,374 |
| 300k, customers scaled | 2.76% | 76.8% | 4.09x | 1,775 |

Added `--customers`, defaulting to a value that scales with `--n`.

**Insufficiency gate could route a certain-fraud case toward release.**
`ARCHITECTURE.md` 1.3 runs the thin-file gate before the confidence check, which
sent a case scored at `p_bad = 1.000` into a step-up that a passed verification
could have released. Reordered: upholding needs no exculpatory evidence because
the block is already in place, so the gate only guards releases.

**Step-up wrote fiction into the record, twice.** The first version zeroed a
customer's past return count when they agreed to prepay. Prepaying removes
future return risk; it does not mean the returns never happened. The second
credited verification as extra prior orders. Same error, and it backfired:
crediting a thin file with three extra orders in the same tenure reads as
velocity, so two customers who verified successfully scored worse afterwards,
`p_bad` 0.204 to 0.571.

Verification now enters at the gate and leaves `p_bad` alone. Asserted by
`test_verification_never_touches_the_record` and
`test_verification_cannot_release_a_bad_case`.

**Step-up ran where it could not help.** Only 2 of 75 exchanges resolved
anything, because most step-ups come from the ambiguous branch where the gate
was never the blocker. Now only thin-file step-ups get an exchange: 4 instead of
75, and LLM calls per 300 cases fell from 592 to 329.

**Citation checker rejected legitimate figures.** Amounts print to the whole
rupee, so a Rs 641.76 order shows as "642" and the checker called it invented.
Now allows the rounded form of any value.

## Deviations from ARCHITECTURE.md

| Section | Spec | Built | Why |
|---|---|---|---|
| 1.3 | Gate before confidence check | Confidence check first | Defect above |
| 1.1 | 7 network features | 8 | `network_instrument_merchants` is the Case-2 signal, missing from the original validation script |
| 1.5 | `metrics.py` writes `METRICS.md` | Split `metrics.py` / `report.py` | 826 lines in one file |
| 1.3 | Thin-file small-amount to ESCALATE | Kept, made configurable | Spends the expensive path on the cheapest cases |
| 2.1 | Step-up adds evidence, re-adjudicates | Adds verification, re-decides | Verification is not history; see Defects |
| | `make metrics` | `python run.py metrics` | `make` is not installed here; a Makefile delegates |

## Tests

61 passed in 4s.

**Leakage (9).** AST check that quarantined modules never import the answer key;
training door raises on holdout ids; no answer-key columns reach the store; no
single feature scores above 0.99 AUC alone; shuffle test collapses holdout AUC
from 0.913 to 0.49; point-in-time counters monotone across 3,401 pairs; tenure
cannot outgrow wall-clock; ablation is a flag not a fork.

**Policy (12).** EV strictly decreasing in `p_bad`; release propensity monotone
at five amounts; margin ceiling at three margins; no confidence level releases a
thin file; confidently-bad upholds a thin file; same `p_bad` different amounts
gives different actions; every decision carries reasons; only OVERTURN moves
money.

**Determinism (10).** Two replays byte-identical; redecide at same config is
identity; moving the operating point moves no probability; release count
monotone in cap; grading and step-up resolution deterministic.

**Agent (28).** Agent reproduces the backtest; agent runs price through the
Phase 1 grader; no LLM module calls `decide()`; `StepUpFacts` cannot name an
action; verification never moves `p_bad`; verification cannot release a bad
case; invented numbers caught; every verdict citation-clean; template output
labelled; ledger chain verifies and tampering breaks it at the right entry;
escalations carry a citation-checked brief; replay mode refuses to invent;
orchestrator deterministic.

Phase 1 definition of done: `METRICS.md` regenerates with one command, yes.
Holdout precision and recall with CIs, yes. Test files green, yes. Moat ablation
at least +0.05 AUC: +0.045 on seed 42, +0.059 averaged across five, so not on
this seed.

## Open

1. Retune the generator so merchant-visible evidence is weaker (local-only is
   0.868 where design intent was ~0.81), or keep the data and rebuild the moat
   argument around shape. Leaning toward the second. This also settles the
   +0.05 gate.
2. No live Claude run yet. The code path is written and the replay cache is
   ready; one live run records it and `replay` then serves the demo off-network.
3. An 88-order, 1,132-day, spotless customer scores 0.22 and abstains where it
   should clear easily.
4. Ambiguous-branch step-ups (73 of 300) have no resolution path. Either give
   verification an EV effect for them or accept they stay abstentions.
5. Whether thin-file small-amount cases should really cost a human review.

## Running it

```bash
cd development
python run.py data300k    # ~31s
python run.py test        # ~4s
python run.py metrics     # ~60s
python run.py agent       # ~10s
python run.py notebooks   # ~90s
python run.py seeds       # ~5min, five worlds end to end
```

Reading order coming back to this cold: `pr/README.md`, then
`notebooks/00_start_here.ipynb`, then `core/policy.py`, then `METRICS.md`
sections 2 and 8, then `tests/test_leakage.py`. `notes.txt` is the chronological
log.

## Not included

`ARCHITECTURE.md` 2.6: the FastAPI service and the four demo screens.
`service/` is an empty scaffold.
