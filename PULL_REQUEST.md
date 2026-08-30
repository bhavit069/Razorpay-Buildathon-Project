# Phases 1 and 2, plus review response

Target branch: `dev`
Scope: `ARCHITECTURE.md` 1.1 to 1.6 and 2.1 to 2.6, with 2.6 cut to a single screen.
Code: [`simulation/`](simulation/)

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

Headline on a 1,775-case temporal holdout at the EV point (cap 0.20):
**88.2% recall of recoverable revenue**, 96.3% overturn
precision, Rs 7.21 cr recovered against
Rs 51.21 L of fraud admitted,
Rs 1.29 cr net contribution,
15.3% abstention.

Those numbers are worse than the previous revision on every axis except recall,
which is the point. See "Review response" below.

Read "How valid is this" before quoting any of it.


## Review response

Ten items. Every one makes a headline number look worse. All taken.

**1. Deployment mode is now declared.** Booking full order value as recovered is
only honest running inline at checkout with the customer still in session. As a
queue review they have already left. `Deployment(inline|queue)` carries a
recontact rate and section 11 sweeps it as a band: at a 70/50/35% return rate
the same decisions are worth Rs 74.64 L / Rs 38.58 L / Rs 11.53 L against
Rs 1.29 cr inline, and breakeven is a 28.6% return rate.
The discount applies to recovery only,
not to fraud admitted, since a fraudster invited back to finish a stolen-card
order is more motivated to return than an honest customer who already bought
elsewhere. Inline also requires the step-up to finish in session; an email round
trip is a queue review wearing an inline costume.

**2. Stopped demoing at cap 0.02.** That value was picked because it flattered
precision, which is the behaviour this project criticises. The report, the demo
and the tests now run at cap 0.20, where EV(release) stops being positive.
Precision fell to 96.3%, fraud admitted roughly doubled,
recall rose to 88.2%. The baselines table shows both rows and
labels the tuned one as existing solely to make the ablation fair.

**3. Recall leads the headline** in bold, with abstention rate and the raw count
of undecided cases beside it.

**4. Leave-one-merchant-out.** Dropped Aurum Jewels from
training and scored only its holdout cases. AUC falls
0.0008, which is nothing. Precision at the same cap
drops 92.9% to
89.0% and contribution by
Rs 31 L. Ranking
transfers; pricing does not. The platform claim holds for the hard part and
fails for the easily fixed part, which is recalibrating on a few hundred of the
new merchant's own cases.

**5. Human-reviewer baseline added** at 87.5%
accuracy, Rs 150 a case,
4 hours. **It beats this system**:
Rs 1.36 cr against
Rs 1.29 cr. What does not survive is that row's
assumption, that a person reviews all 1,775 cases. Rationed rows follow below.

**6. Generator retune: closed by reframing.** The moat claim is that network
evidence releases more orders at higher precision at the same time, and a
threshold move cannot do both. The AUC ablation is a diagnostic in section 4.

**7. Kolkata miss: closed, not a bug.** An 88-order spotless file scoring 0.22 is
the bust-out prior working. 42% of fraudsters in this data farm clean history on
purpose, so a long clean record cannot be a free pass.

**8. Live model run: done, partially.** A Gemini key arrived, so `llm.py` now has
two providers behind the same `complete()` entry point; the Anthropic path is
untouched and the cache key includes provider and model. Three demo cases carry
real, citation-checked Gemini verdicts, cached, replayable off-network. The rest
hit the free tier's 20-requests-per-day-per-model cap.

Four environment problems and one real bug, all in `notes.txt`. The bug matters:
the citation checker was pulling digits out of payment ids, so
`pay_5E72cODtQrmZkn` contributed "72", and every correctly-cited model verdict
failed three times and fell back to the template. Offline runs never caught it
because the template does not print the payment id. "300 of 300 audit clean" was
measuring a checker that would have rejected real output. Fixed; it still
catches fabrication.

**9. Docs emit rather than get patched.** `artifacts/tables.md` is generated.
Showcase cases are selected by predicate in `core/showcase.py`: six roles
described by shape, matched against holdout, so the list survives a reseed and
no train-split case can appear.

**10. UI cut to one screen.** `service/case_room.py` writes a single
self-contained HTML file with the case list, local against network evidence,
verdict, tool trace and transcript. No server, no build step. The frontier and
portfolio views stay in `METRICS.md` and the notebooks.

## Second review response

Three items, then frozen. Each one makes something worse or exposes something
that was quietly wrong. A fourth turned up while checking the demo.

**A. The queue haircut was checked, and it is not a bug.** A 35% recontact rate
turning Rs 1.29 cr into Rs 11.53 L looked like a double discount. It is not. The
discount is applied once, to recovery, and net contribution is computed from the
already-discounted figure. Section 11 now prints every term and every row of the
arithmetic, and `tests/test_accounting.py` asserts the published rows reproduce
`grade()` to the paisa.

The reason a 65% cut in recovery costs 91% of contribution is that only the
first term scales:

    net contribution = m * R_gross * rate  -  A  -  f * n_bad  -  reviews
                     = 0.25 * 7,21,27,282 * rate  -  51,58,053

Margin is 25% of a recovered rupee; fraud is 100% of an admitted one. So a fixed
Rs 51.58 L of drag is subtracted from a quarter of a shrinking number, and
contribution is roughly 4x as sensitive to the rate as revenue is. At cap 0.20
the cushion is only 3.5x, which is a real property of the operating point rather
than an artefact.

Two things changed as a result. The rate is reported as a band (70/50/35)
instead of one pessimistic point, because an in-session retry prompt is not a
next-day email. And breakeven, 28.6%, is reported alongside it, since that is
the one figure that does not depend on guessing the right point in the band.

The asymmetry, that fraudsters return more readily than honest customers, is now
a declared assumption at the top of section 11 and in the `Deployment` docstring
rather than a constant buried in a dataclass. It is asserted, not measured, and
it is asserted in the pessimistic direction.

**B. A realistic-coverage reviewer baseline.** The existing row assumed a person
reviews all 1,775 cases. Nobody does. Real queues rank by order value and work
down until the day runs out; everything below the line stays blocked and
recovers nothing, because nobody undid the block. Same reviewer, same 87.5%
accuracy, charged only for the cases reached:

| Reviewer coverage | Cases reviewed | Recall of recoverable | Net contribution |
|---|---|---|---|
| all 1,775 | 1,775 | 88.3% | Rs 1.36 cr |
| top 3% by value | 53 | 2.4% | Rs 28.17 L |
| top 10% by value | 178 | 8.6% | Rs 69.41 L |
| **this system, all 1,775** | **1,775** | **88.2%** | **Rs 1.29 cr** |

The full-review row stays, because losing to it is the honest result. The two
together are the actual claim: a reviewer beats this case for case, nobody can
afford to run one across the whole pile, so in practice most cases are never
reviewed at all. The gap is coverage, not judgment.

**C. `METRICS.md` frozen, and freezing it found a bad number.** Regenerated at
cap 0.20 with recall and abstention in the headline, fraud admitted beside every
recovery figure, both reviewer rows, LOMO, the deployment assumption block, and
the between-seed range for money.

Adding that last one exposed a mismatch. The seed sweep re-picked the cap on
each seed's own calibration slice, so the range it produced described a
different policy from the one in the headline, and re-tuning per seed absorbs
exactly the variation being measured. Every seed is now graded twice, and the
headline quotes the shipped row:

| | tuned cap (what was quoted before) | shipped cap 0.20 (what is quoted now) |
|---|---|---|
| net contribution | Rs 1.15 cr to Rs 1.87 cr | Rs 89.95 L to Rs 1.86 cr |
| precision | 97.8% to 99.1% | 92.0% to 98.2% |
| recall | not reported | 83.1% to 89.6% |
| fraud admitted | not reported | Rs 8.13 L to Rs 51.21 L |

So the claim "precision is the stable number" is withdrawn. At a fixed operating
point it moves 6.2 points across five worlds, and recall is the steadiest of the
three. Seed 42, used everywhere else, is a middling draw on revenue and the
worst of the five on cost: no other seed admits more fraud.

**D. The demo screen was showing no demo cases.** Not on the list, found while
checking the case room actually replays the recorded verdicts. `collect()` ran
the orchestrator over the first 400 holdout cases in chronological order and
then tagged whichever of them happened to match a showcase role. Five of the six
showcase cases sit past position 400. So the page rendered one tagged case out
of six, none of the three recorded Gemini verdicts were ever requested, and
every verdict on the demo screen was a template.

Nothing threw, no test failed, the page looked fine. It was only visible by
counting verdict sources in the generated HTML. The header had been printing
"1 tagged as demo cases" the whole time.

Showcase cases are now inserted explicitly and the stream fills the rest of the
limit. Three of the six carry real model output, including the spotless-record
case that gets upheld, which is the one worth opening first. Two tests added:
the case room must contain every showcase case, and at least one verdict on it
must come from the cache rather than a template.

Also in this pass: `GEMINI_API KEY` renamed to `GEMINI_API_KEY` (the space meant
no shell would ever have loaded it), `run.py room` and `run.py warm` added so
the case room and the cache warmer are reachable the same way as everything
else, `artifacts/tables.md` and `artifacts/case_room.html` un-ignored so a
cloner can open the demo without running anything, em dashes removed from
`DATA_CARD.md`, and 13 tests added covering the accounting and the demo screen.

## Pre-demo pass

Four must-do items, and a fifth that fell out of doing them.

**A. IDEA.md and DATA_CARD.md regenerate now.** Both carried hand-typed figures
from the 100k dataset and an earlier seed, and hand-patching them is how they
got wrong in the first place. `core/docs.py` fills seven marked blocks in place,
so the prose stays hand-written and the numbers stop being: portfolio context,
the six worked examples, the frontier, the results table, the calibration
anchors, the file table, the outcome and false-positive mix.
`python -m core.docs --check` exits non-zero if either document has drifted.

IDEA.md section 5 was the worst of it. Five payment ids picked by hand, five of
the six in the train split, quoting rupee figures from a generator that has
changed twice since. It prints whatever `core/showcase.py` selects out of
holdout now, so it cannot drift back.

The ACQUIT rename was already complete. The only two occurrences left are a
line in `notes.txt` recording that the rename happened and a note in
`knowledge.txt` telling a reader to ignore the name if they meet it in an older
draft.

**B. Two demo dry runs, off-network, no code changes.** `dry_run.py` replaces
`socket.socket` with something that raises, then does everything the demo does:
generated doc blocks current, suite green, model fits, six cases selected from
holdout, agent over 300 orders, agent reproduces the deterministic backtest to
the digit, case room builds with every demo case on it and model-backed, page
loads nothing external, replay mode serves the demo cases from cache. Twelve
checks. Run twice, clean both times, no code change needed between them.

**C. Verdict provenance is on the screen.** The page used to print
`verdict source: cache` in small grey text, which is honest and useless. Each
verdict now carries a green badge naming the model or an amber one reading
TEMPLATE, NOT MODEL OUTPUT, a sentence underneath saying which it is in words,
a MODEL/TMPL tag in the case list, and a header stating the split. A screenshot
of the page cannot imply model output that is not there.

Threading provenance through turned up two dead strings. `Completion` never
produced a source called `claude`, so the orchestrator's summary bucket for it
always read zero, and `stepup.py` tested `sources & {"claude", "cache"}` and
reported `template` for exchanges a model had actually written. The test meant
to catch this asserted that every verdict was a template, which held only while
the cache was empty.

**D. All six showcase cases are warmed.** The daily quota had rolled over, so
the three that were still templates went through on the first attempt. Every
demo case now carries a real, citation-checked verdict, cached and replayed
off-network.

**E. Metric story order, everywhere it appears.** Recall is the steady quantity
at 83.1% to 89.6%. Precision moves 6.2 points, 92.0% to 98.2%. Money moves
about twofold. Each depends on something different: recall on ranking,
precision on where a fixed threshold lands in a particular world, money on that
world's value distribution. "Precision is stable" is withdrawn from every
document that still carried it. Section 11 now leads with the 28.6% breakeven
rather than a guessed 35% recontact rate, because a threshold is defensible and
a guessed parameter is not.

Also in this pass: `run.py docs`, `run.py dry`, `run.py room` and `run.py warm`;
`IDEA.md` and `ARCHITECTURE.md` added to the repo, since `README.md` referenced
them and they were not here; em dashes removed from both; `SCRIPT.txt` with the
five demo beats; and `flow.html`, a browser walkthrough of the pipeline that
follows one real case through all eight stages. 76 tests.

## Files

`simulation/core/`, 2,286 lines:

| Module | Does |
|---|---|
| `truth.py` | Only module that opens the answer key |
| `feature_store.py` | Evidence assembly and validation |
| `model.py` | LightGBM plus isotonic calibration |
| `policy.py` | EV rule over four actions |
| `backtest.py` | Chronological replay, self-describing ledger |
| `metrics.py` | Grading, bootstrap CIs, baselines, frontier |
| `report.py` | Writes `METRICS.md` and three figures |
| `showcase.py` | Picks demo cases by predicate, not by payment id |

`simulation/agent/`, 1,151 lines:

| Module | Lines | Does |
|---|---:|---|
| `ledger.py` | 89 | Append-only log, each entry hashing the previous |
| `llm.py` | 218 | Anthropic and Gemini behind one entry point, disk replay cache |
| `tools.py` | 102 | The agent's only route to the model and policy |
| `verdict.py` | 301 | Case notes and escalation briefs, citation-checked |
| `stepup.py` | 227 | Verification exchange and fact extraction |
| `orchestrator.py` | 173 | The case loop |

`simulation/tests/`, 76 tests, 11s: leakage 11, policy 12, determinism 10,
agent 29, accounting and demo screen 14.

`simulation/core/docs.py` regenerates the marked blocks in `IDEA.md` and
`DATA_CARD.md`. `simulation/dry_run.py` is the pre-demo check.

`simulation/service/case_room.py` writes the one demo screen.

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

Each seed is graded at the shipped cap 0.20, the operating point everything
else on this page is priced at:

| seed | holdout | local AUC | +network AUC | lift | precision | recall | fraud admitted | net contribution |
|---|---|---|---|---|---|---|---|---|
| 42 | 1775 | 0.8682 | 0.9134 | +0.0452 | 96.3% | 88.2% | Rs 51.21 L | Rs 1.29 cr |
| 1 | 1745 | 0.8501 | 0.9209 | +0.0709 | 97.6% | 89.3% | Rs 16.63 L | Rs 1.86 cr |
| 2 | 1486 | 0.8414 | 0.9066 | +0.0651 | 92.0% | 89.6% | Rs 41.43 L | Rs 89.95 L |
| 3 | 1826 | 0.8602 | 0.9321 | +0.0719 | 95.6% | 87.0% | Rs 38.30 L | Rs 1.51 cr |
| 4 | 1800 | 0.8678 | 0.9109 | +0.0431 | 98.2% | 83.1% | Rs 8.13 L | Rs 1.18 cr |

Net contribution ranges Rs 89.95 L to Rs 1.86 cr, sd Rs 32.37 L. That spread is
wider than the bootstrap interval in `METRICS.md` 1, so the limit is how the
world was generated, not how many holdout cases there are.

Precision does not hold, and an earlier revision of this document said it did.
At a fixed operating point it runs 92.0% to 98.2%, a 6.2 point spread, and
recall is the steadiest of the three at 83.1% to 89.6%. The old claim of 97.8%
to 99.1% came from a sweep that re-picked the cap on each seed, which absorbs
the variation it was supposed to be measuring. See "Second review response" C.

AUC lift ranges +0.043 to +0.072, mean +0.059. Seed 42, used throughout, is a
middling draw on revenue and the worst of the five on cost.

Quote all of it as a range.

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
`simulation/README.md`.

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

76 passed in 11s.

**Leakage (11).** AST check that quarantined modules never import the answer key;
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

**Agent (29).** Agent reproduces the backtest; agent runs price through the
Phase 1 grader; no LLM module calls `decide()`; `StepUpFacts` cannot name an
action; verification never moves `p_bad`; verification cannot release a bad
case; invented numbers caught; every verdict citation-clean; template output
labelled; ledger chain verifies and tampering breaks it at the right entry;
escalations carry a citation-checked brief; replay mode refuses to invent;
orchestrator deterministic.

**Accounting and the demo screen (14).** Recontact discount applied exactly once
at every rate; fraud admitted does not move with the rate; every published row
of the section 11 arithmetic reproduces `grade()`; breakeven rate lands at zero
contribution; fixed drag is fixed; the rationed reviewer reaches only the top
slice by value; unreviewed cases recover nothing; more coverage never recovers
less; review cost is charged only for cases reviewed; the rationed reviewer at
100% coverage equals the unrationed one; a full-coverage reviewer beats this
system and every rationed one loses to it; the case room contains every showcase
case; at least one verdict on it comes from a recorded model reply; every rendered case declares its provenance and the header states the split.

Phase 1 definition of done: `METRICS.md` regenerates with one command, yes.
Holdout precision and recall with CIs, yes. Test files green, yes. Moat ablation
at least +0.05 AUC: +0.045 on seed 42, +0.059 averaged across five, so not on
this seed.

## Open

Items 1 and 3 of the previous revision are closed: the generator is kept and
the moat argument is rebuilt around shape (review item 6), and the spotless
customer scoring 0.22 is the bust-out prior working as designed (review item 7).
What is left:

1. No live Anthropic run yet. That path is written and is the documented
   default, but no Anthropic key exists on this machine, so only the Gemini
   path has actually executed. Three demo cases are recorded and replay
   off-network; wider coverage needs a paid key, not code.
2. Ambiguous-branch step-ups (73 of 300) have no resolution path. Either give
   verification an EV effect for them or accept they stay abstentions. This is
   the largest open item: it means a human still touches about 4% of the pile
   and nothing here reduces that.
3. Whether thin-file small-amount cases should really cost a human review.
4. Step-up pass rates are assumed, not measured. Both are stated and both are
   swept in `METRICS.md` 5, but real numbers need a pilot.
5. No latency or throughput measurement. The case loop runs at a p50 under a
   millisecond, but inline deployment lives or dies on p99 and that is not
   measured here.
6. Reviewer accuracy of 87.5% is the midpoint of a published range, not
   something measured. The baseline in section 2 is only as good as that number,
   and it is the baseline that beats us.

## Running it

```bash
cd simulation
pip install -r requirements.txt
python run.py data300k    # ~31s, required first
python run.py test        # ~8s
python run.py metrics     # ~60s
python run.py room        # ~15s, then open artifacts/case_room.html
python run.py agent       # ~10s
python run.py notebooks   # ~90s
python run.py seeds       # ~6min, five worlds end to end
```

`artifacts/case_room.html` is committed, so a reader can open the demo without
running anything first.

Reading order coming back to this cold: `README.md`, then the case room, then
`notebooks/00_start_here.ipynb`, then `core/policy.py`, then `METRICS.md`
sections 1, 2, 8 and 11, then `tests/test_leakage.py`. `notes.txt` is the
chronological log and is the only document written in the first person.

## Not included

Three of the four screens in `ARCHITECTURE.md` 2.6, cut on review. There is no
FastAPI service: the case room is a static file, which is one less thing to fail
during a demo.

Full live coverage. Three demo cases have real model verdicts; the rest run on
labelled templates until the daily quota resets or billing is enabled.
