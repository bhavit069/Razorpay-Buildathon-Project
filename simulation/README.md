# Development

Working repo. `../simulation/` is the earlier code, kept as reference. Nothing
here imports from it.

## Setup

Runs on the global interpreter, Python 3.14.6. Everything Phase 1 needs is
installed: numpy 2.5.0, pandas 3.0.3, scikit-learn 1.9.0, lightgbm 4.7.0, scipy,
matplotlib, pytest. `requirements.txt` pins the floors. Phase 2 deps are
commented out.

## Commands

`make` isn't installed here, so `run.py` is the entry point. The `Makefile`
delegates to it so the commands in `ARCHITECTURE.md` work where make exists.

```
python run.py data        # 100k baseline, reproduces the committed stats
python run.py data300k    # 300k working set, ~31s
python run.py validate    # signal check and network ablation
python run.py moat        # the naive shared-threshold ablation, see below
python run.py sweep       # intercept sensitivity across block-rate regimes
python run.py metrics     # regenerate METRICS.md and artifacts/
python run.py test        # 33 tests, ~4s
python run.py notebooks   # rebuild and re-execute the explainers
python run.py seeds       # rerun the pipeline on five generated worlds, ~5min
python run.py agent       # phase 2: run the agent over the holdout docket
python run.py clean
```

Output is forced to UTF-8. The scripts print rupee symbols and Windows consoles
default to cp1252, which raises `UnicodeEncodeError`. Anything new that prints
rupees goes through `run.py` or sets `PYTHONUTF8=1` itself.

## Reproduction

`python run.py data` reproduces `../simulation/stats.json` exactly: 2,817
blocked, 75.54% FP share, ₹14.79 cr wrongly blocked, 622 holdout blocked.
`appeal_queue.csv` differs from the archived copy only in the final float digit
of `f_amount_z` on 2 of 2,817 rows.

## Generator change

Added `--customers`, defaulting to a value that scales with `--n`.

`n_customers` was a fixed `Config` default of 12,000 that `--n` never touched,
so the Day-1 instruction in `ARCHITECTURE.md` to regenerate at `--n 300000`
tripled orders-per-customer from 8.3 to 25 and broke the calibration:

| | block rate | FP share | value ratio | holdout blocked |
|---|---|---|---|---|
| 100k baseline | 2.82% | 75.5% | 3.79x | 622 |
| 300k, customers fixed at 12k | 6.31% | 84.2% | 5.09x | 5,374 |
| 300k, customers scaled to 36k | 2.76% | 76.8% | 4.09x | 1,775 |

The 100k baseline is unaffected. `data300k/` is the working set.

## Stale numbers in the design docs

`DATA_CARD.md` and `IDEA.md` were written against an earlier generator. Don't
quote them.

`DATA_CARD.md` 3-4: appeal queue 2,722 rows (actual 2,817), disputes 2,693
(2,616), refunds 5,762 (5,844), holdout blocked 615 (622), and every block rate
in the sensitivity table (default row says 2.40%, actual 2.82%).

`IDEA.md` 4.1, the moat table:

| | doc | 100k | 300k |
|---|---|---|---|
| local-only AUC | 0.813 | 0.855 | 0.868 |
| local+network AUC | 0.920 | 0.917 | 0.913 |
| AUC lift | +0.107 | +0.062 | +0.045 |
| AP lift | +0.244 | +0.166 | +0.144 |

`local+network` reproduces closely. The gap is that local-only is stronger than
documented, so the shipped generator leaks more persona through
merchant-visible observables than the version `IDEA.md` 8 describes tuning down
to 0.81. More training data widens it: local-only climbs while local+network
stays flat.

The operating-point table in `IDEA.md` 7 is stale in both directions and sweeps
a threshold to 0.50, which an EV policy cannot reach. See the margin ceiling
below.

## The moat

An earlier version of this file put the moat at +63% of net recovery, measured
by comparing the two models at a shared raw-probability threshold. That was
wrong: it held the threshold fixed across models whose raw scores sit on
different scales, so most of the apparent gap was miscalibration in the local
model rather than missing evidence.

With both models isotonic-calibrated and each given its own operating point
chosen on the calibration slice:

| Evidence | cap | Released | Precision | Net contribution |
|---|---|---|---|---|
| Merchant-local only | 0.005 | 993 | 98.3% | ₹1.47 cr |
| Network only | 0.050 | 802 | 98.1% | ₹1.43 cr |
| Local + network | 0.020 | 1,181 | 98.6% | ₹1.53 cr |

+₹6.76 L, +4.6%. The claim that survives is about shape rather than size:
network evidence releases 188 more orders at higher precision at the same time.
A threshold move trades one against the other.

`python run.py moat` still prints the shared-threshold comparison. Keeping it
makes the difference between the two measurements inspectable.

## Phase 1

`ARCHITECTURE.md` 1.1 to 1.6 complete.

| Module | Job |
|---|---|
| [core/truth.py](core/truth.py) | Only module that opens the answer key. `training_labels()` raises on a holdout id, `grade()` is unrestricted. |
| [core/feature_store.py](core/feature_store.py) | Evidence in three named blocks. Ablation is one argument. |
| [core/model.py](core/model.py) | LightGBM plus isotonic calibration on the last 20% of train. Fixed hyperparameters. |
| [core/policy.py](core/policy.py) | EV rule, four actions. |
| [core/backtest.py](core/backtest.py) | Chronological replay, self-describing ledger, byte-identical across runs. |
| [core/metrics.py](core/metrics.py) | Grading, bootstrap CIs, baselines, frontier, per-merchant caps. |
| [core/report.py](core/report.py) | Writes `METRICS.md`. 1.5 puts this inside metrics.py; split so one file is arithmetic and one is prose. |

Headline at cap=0.02 on the 300k holdout: 98.6% precision, 81.1% recall of
recoverable, ₹7.04 cr recovered against ₹22.57 L admitted, ₹1.53 cr net
contribution, 17% abstention. CIs in `METRICS.md` 1.

### Two deviations

Confidently-bad is checked before the insufficiency gate. 1.3 runs the gate
first, which sent a case scored at `p_bad = 1.000` into a step-up dialogue that
a passed verification could have released. Upholding needs no exculpatory
evidence because the block is already in place, so the gate guards releases
only.

Two rupee columns. Net (gross convention) is recovered minus admitted, how the
industry and `IDEA.md` quote it. Net contribution is margin on recovered sales
minus the full basket on bad ones, which is what reaches the P&L and what the
policy optimises. Release-everything scores ₹5.51 cr on the first and −₹8.79 L
on the second.

### Margin ceiling

`EV(release) > 0` requires `p_bad < m/(1+m)`, independent of order size. At the
default 25% margin that is 0.20, so `cap` above 0.20 does nothing. Margin bounds
how much doubt a release can carry, not risk appetite.

## How much to trust the numbers

`python run.py seeds` reruns the whole pipeline on five independently generated
worlds. Net contribution ranges ₹1.15 cr to ₹1.87 cr, sd ₹30 L. That spread is
wider than the bootstrap interval inside any single world, so the rupee figures
are limited by the generator rather than the holdout size. Precision is stable
at 97.8% to 99.1%. AUC lift ranges +0.043 to +0.072, mean +0.059. Seed 42, used
everywhere else, is the least favourable of the five.

Quote precision as a number and money as a range.

Other things worth knowing before quoting anything:

- 77% of holdout value is one merchant, Aurum Jewels, 451 of 1,775 cases.
- Bootstrap intervals resample customers, not cases: 36% of holdout cases share
  a customer. This changed the intervals by under 10%, but resampling cases was
  wrong in principle.
- The holdout is read many times to report. Nothing is chosen on it; operating
  points come from the calibration slice.
- `network_clean_rate` carries 0.346 of gain at rank 1, against the ~0.49
  `DATA_CARD.md` 6.4 warns about. Confirmed, not resolved. LightGBM's default
  split-count importance ranks it sixth at 0.085 and would have hidden this.

## Notebooks

Five in [notebooks/](notebooks/), generated and executed by
`python run.py notebooks` so outputs cannot drift from the code.

| Notebook | Covers |
|---|---|
| `00_start_here.ipynb` | Repo map, whole pipeline in one cell |
| `01_the_data.ipynb` | Why the dataset isn't circular, signal overlap, stale-docs audit |
| `02_the_moat.ipynb` | The moat measured three ways and why two mislead |
| `03_the_decision_system.ipynb` | Calibration, the EV rule, the margin ceiling, four actions |
| `04_worked_cases.ipynb` | The six `IDEA.md` cases re-checked |

Notebook 04 found that five of the six showcase cases are in the train split,
and the model is fitted on the first 80% of it. It picks holdout substitutes for
each narrative role. It also shows Case 3 doesn't reproduce: an 88-order,
1,132-day, spotless file scores 0.22 and abstains where the pitch says it
overturns. That is a model miss and the most interesting open bug.

## Log

[notes.txt](notes.txt), chronological.

## Phase 2

The agent plane. Adds language, interaction and audit; adds no decision logic.

| Module | Job |
|---|---|
| [agent/ledger.py](agent/ledger.py) | Append-only log, each entry hashing the previous one |
| [agent/llm.py](agent/llm.py) | Claude access with a disk replay cache; live / replay / offline |
| [agent/tools.py](agent/tools.py) | The only path from the agent to the model and policy |
| [agent/verdict.py](agent/verdict.py) | Writes the case note, then checks every number in it |
| [agent/stepup.py](agent/stepup.py) | Verification exchange and structured fact extraction |
| [agent/orchestrator.py](agent/orchestrator.py) | The case loop |

`python run.py agent` runs it. On 300 holdout cases at cap=0.02: 151 released at
99.3% correct, 75 upheld, 73 held for verification, 1 escalated, all 300 verdicts
audit-clean, ledger verifies.

**There is no API key on this machine, so nothing has been through Claude yet.**
Everything runs in `offline` mode against deterministic templates, tagged
`source="template"` so they can never be mistaken for model output. One live run
records the cache and `replay` mode then serves the demo off-network.

### Verification is not evidence

The step-up mechanism was wrong twice before it was right. The first version
zeroed a customer's past return count when they agreed to prepay; the second
credited verification as extra prior orders. Both falsify the record, and the
second backfired: crediting a thin file with three extra orders in the same
tenure reads as velocity, so two customers who verified successfully scored
*worse* afterwards (p_bad 0.204 to 0.571).

Verification now goes to the insufficiency gate, which is the thing actually
asking whether we can identify this person, and leaves `p_bad` untouched. The
record did not change, so the estimate of the record must not either. Asserted
by `test_verification_never_touches_the_record`.

Only insufficiency step-ups get an exchange. Ambiguous-branch cases already have
a full record, so verification cannot move them; running one anyway cost 73
dialogues to resolve 2 cases.

## State

Phase 1 and Phase 2 core done. `service/` and the UI are not started. `agent/` and `service/` are empty scaffolds and there is no LLM in
this codebase. `datagen/moat_ledger.py` duplicates what `core/metrics.py` does
properly and is kept only for the comparison above.
