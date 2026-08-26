#!/usr/bin/env python3
"""
Builds and executes the explainer notebooks.

The notebooks are generated rather than hand-edited so their outputs can never
drift from the code they describe: re-run this after changing anything in core/
and every number in every notebook is recomputed.

    python notebooks/build_notebooks.py            # build + execute all
    python notebooks/build_notebooks.py --no-exec  # build only (fast)
"""
from __future__ import annotations

import argparse
import os
import sys

import nbformat as nbf
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

PRELUDE = """\
import sys, os
sys.path.insert(0, os.path.abspath('..'))
os.environ['PYTHONUTF8'] = '1'
import numpy as np, warnings
warnings.filterwarnings('ignore')
DATA = '../data300k'      # the working set
DATA100K = '../data'      # the calibrated baseline the pitch quotes
"""


def md(t):
    return new_markdown_cell(t.strip())


def code(t):
    return new_code_cell(t.strip())


# ---------------------------------------------------------------------------
NOTEBOOKS = {}

NOTEBOOKS["00_start_here.ipynb"] = [
    md("""
# 00 - Start here

**What this project is.** A merchant's fraud stack blocks an order. Nobody ever
looks at that decision again, and no row is written anywhere saying "we just
refused a good customer." This system opens a case on every blocked order,
pulls cross-merchant evidence the merchant cannot see, and decides whether the
block should stand.

**What is built right now.** Phase 1 -- the deterministic decision core -- is
complete. There is no LLM anywhere in this codebase yet. Phase 2, the agent
that writes verdicts and runs verification dialogues, is not started.

| Layer | Module | Job |
|---|---|---|
| Data | `datagen/generate.py` | Invents a year of payment traffic and a risk stack that blocks some of it |
| Evidence | `core/feature_store.py` | Assembles one case's evidence in three named blocks |
| Answer key | `core/truth.py` | The only door to ground truth, and it is locked during training |
| Model | `core/model.py` | P(this block was correct), calibrated |
| Policy | `core/policy.py` | Turns that probability into a decision using money |
| Replay | `core/backtest.py` | Runs the year chronologically and writes a ledger |
| Grading | `core/metrics.py` | Joins truth afterwards and prices every decision |
| Report | `core/report.py` | Writes `METRICS.md` |

The rest of these notebooks walk the chain in order. Nothing below is typed by
hand -- every number is computed live when the notebook runs.
"""),
    code(PRELUDE),
    code("""
from core.feature_store import FeatureStore
from core.truth import TruthVault
from core.model import Adjudicator
from core.policy import PolicyConfig
from core.backtest import run
from core.metrics import grade, StepUpModel

store = FeatureStore.load(DATA)
vault = TruthVault(DATA)
print(store)
print(f'answer key holds {len(vault):,} payments (only ~{len(store):,} of them were blocked)')
"""),
    md("""
## The whole pipeline, in one cell

Load evidence, score it, decide, replay, grade. Five lines. Everything after
this notebook is an explanation of one of them.
"""),
    code("""
model  = Adjudicator().fit(store, vault)          # 1. learn, on train only
cfg    = PolicyConfig(cap=0.02)                    # 2. an operating point
ledger = run(store, model, cfg, 'holdout')         # 3. replay the blocked pile
out    = grade(ledger, vault, StepUpModel())       # 4. join truth, price it

print(f'{out.n_cases} appealed cases -> {out.n_released} released')
print(f'precision {out.precision:.1%}   recall of recoverable {out.recall_recoverable:.1%}')
print(f'recovered Rs {out.recovered_inr/1e7:.2f} cr')
print(f'fraud admitted Rs {out.fraud_admitted_inr/1e5:.2f} L')
print(f'net contribution Rs {out.net_contribution_inr/1e7:.2f} cr')
print(f'abstained on {out.abstention_rate:.1%} of cases')
"""),
    md("""
That last line matters as much as the first. The system refuses to decide on a
meaningful share of cases, and that is a designed behaviour, not a gap. See
notebook 03.
"""),
]

# ---------------------------------------------------------------------------
NOTEBOOKS["01_the_data.ipynb"] = [
    md("""
# 01 - The data, and why it is not circular

Everything here is synthetic. The interesting question is not "is it real" --
it is not -- but "does it beg the question it is supposed to answer."

Most synthetic fraud datasets do. The author labels rows as fraud, injects
features that mark them, trains a model that rediscovers the labels, and
reports a precision that measures nothing but the author's imagination.

This generator avoids that in one specific way: **false positives are never
injected. They emerge.**
"""),
    code(PRELUDE),
    code("""
import json
stats100 = json.load(open(f'{DATA100K}/stats.json'))['stats']
stats300 = json.load(open(f'{DATA}/stats.json'))['stats']

for name, s in [('100k baseline', stats100), ('300k working set', stats300)]:
    print(f'--- {name} ---')
    print(f\"  payments            {s['payments']:,}\")
    print(f\"  blocked by the stack{s['blocked']:>8,}  ({s['block_rate']:.2%})\")
    print(f\"  of those, GOOD      {s['blocked_that_were_good']:>8,}  ({s['fp_share_of_blocked_pile']:.1%} of the pile)\")
    print(f\"  revenue refused     Rs {s['revenue_wrongly_blocked_inr']/1e7:>8,.2f} cr\")
    print(f\"  fraud caught        Rs {s['fraud_correctly_blocked_inr']/1e7:>8,.2f} cr\")
    print(f\"  value ratio         {s['fp_to_tp_value_ratio']}x\")
"""),
    md("""
## Two stages that never talk to each other

**Stage 1** invents the true world. Every customer carries a hidden persona and
every order carries a true counterfactual outcome -- what *would* have happened
if it had been allowed through.

**Stage 2** runs a merchant scorecard that never sees persona or outcome. It
sees a dozen observable signals: new device, address mismatch, odd hour,
unusual basket, high-RTO pincode, disposable email.

Honest-but-atypical customers emit the *same observable signals* as fraudsters.
So the scorecard blocks some of them. That mismatch is the false-positive
population. No parameter anywhere sets a false-positive rate.
"""),
    code("""
from core.feature_store import FeatureStore, LOCAL_FEATURES, NETWORK_FEATURES
from core.truth import TruthVault

store, vault = FeatureStore.load(DATA), TruthVault(DATA)
cases = store.split('holdout')
y = vault.labels(store.payment_ids(cases))    # 1 = the block was correct

print(f'{len(LOCAL_FEATURES)} local features the merchant can see:')
print('   ', ', '.join(LOCAL_FEATURES))
print(f'\\n{len(NETWORK_FEATURES)} network features only the platform can see:')
print('   ', ', '.join(NETWORK_FEATURES))
print(f'\\nholdout: {len(cases)} appealed cases, {(y==0).sum()} of them good ({(y==0).mean():.1%})')
"""),
    md("""
## The overlap is the whole problem

If the fraud tells separated cleanly, this project would be trivial. Here is
what a merchant actually sees when it looks at a good customer versus a bad one
in its blocked pile.
"""),
    code("""
import numpy as np
X = store.as_matrix(cases, ('local',))
names = LOCAL_FEATURES
print(f'{\"signal\":<28}{\"good (FP)\":>12}{\"bad (TP)\":>12}   overlap')
print('-'*70)
for j, n in enumerate(names):
    if n in ('amount', 'risk_score', 'f_amount_z'): continue
    g, b = X[y==0, j].mean(), X[y==1, j].mean()
    bar = '#' * int(30 * min(g, b) / max(g, b, 1e-9))
    print(f'{n:<28}{g:>12.3f}{b:>12.3f}   {bar}')
"""),
    md("""
Read the overlap column. `f_disposable_email` fires for honest people who
protect their inbox. `f_device_is_new` fires for anyone who bought a phone.
`f_pincode_rto_propensity` is high across Tier-2 and Tier-3 India -- which is
to say, a risk stack tuned on regional RTO rates refuses customers in Patna and
Guwahati more often than customers in Bengaluru.

Each of those is individually defensible. In aggregate they are a merchant
quietly withdrawing from the fastest-growing part of its market.
"""),
    code("""
# Which stated block reasons produce the most wrongful declines?
from collections import defaultdict
agg = defaultdict(lambda: [0, 0, 0.0])
for e, bad in zip(cases, y):
    a = agg[e.meta['block_reason']]
    a[0] += 1
    a[1] += (bad == 0)
    a[2] += e.amount_inr * (bad == 0)

print(f'{\"block reason\":<24}{\"blocked\":>9}{\"wrongly\":>9}{\"FP rate\":>9}{\"Rs refused\":>14}')
print('-'*68)
for r, (n, fp, amt) in sorted(agg.items(), key=lambda kv: -kv[1][2]):
    print(f'{r:<24}{n:>9}{fp:>9}{fp/n:>9.0%}{amt/1e5:>12,.1f} L')
"""),
    md("""
## Calibration, and where the docs are stale

The generator was tuned against public anchors. Two of them:

- Block rate ~2.7% (ClearSale, US domestic, Q3 2023) - we sit at ~2.8%.
- 30-70% of merchant-declined orders are actually good (Signifyd) - we sit at
  ~76%, *above* the published band. Stated, not hidden.

**Watch out:** `DATA_CARD.md` and `IDEA.md` were written against an older
version of this generator. Their row counts and AUC tables no longer match what
the code produces. `../README.md` has the full audit.
"""),
    code("""
card_claims = {'appeal_queue rows': 2722, 'disputes': 2693, 'refunds': 5762, 'holdout blocked': 615}
actual = {'appeal_queue rows': stats100['blocked'], 'disputes': stats100['disputes'],
          'refunds': stats100['refunds_rto'], 'holdout blocked': stats100['holdout_blocked']}
print(f'{\"\":<22}{\"DATA_CARD says\":>16}{\"actually\":>12}')
for k in card_claims:
    flag = '' if card_claims[k] == actual[k] else '   <-- stale'
    print(f'{k:<22}{card_claims[k]:>16,}{actual[k]:>12,}{flag}')
"""),
]

# ---------------------------------------------------------------------------
NOTEBOOKS["02_the_moat.ipynb"] = [
    md("""
# 02 - The moat, and how big it actually is

The load-bearing claim of the whole pitch:

> The customer a merchant just declined as a stranger is not a stranger to the
> platform.

A merchant scoring a first-time order sees a thin file and blocks it, correctly
given what it can see. The payment platform sees the same identity across forty
merchants, three years and eight hundred clean orders.

This notebook measures that advantage three ways and gets three different
answers. Which one you quote matters.
"""),
    code(PRELUDE),
    code("""
from sklearn.metrics import roc_auc_score, average_precision_score, brier_score_loss
from core.feature_store import FeatureStore
from core.truth import TruthVault
from core.model import Adjudicator

store, vault = FeatureStore.load(DATA), TruthVault(DATA)
ho = store.split('holdout')
y  = vault.labels(store.payment_ids(ho))

models = {}
print(f'{\"evidence\":<18}{\"AUC\":>8}{\"AP\":>8}{\"Brier\":>9}')
print('-'*45)
for blocks in [('local',), ('network',), ('local','network')]:
    m = Adjudicator(blocks).fit(store, vault)
    models[blocks] = m
    p = m.predict(store, ho)
    print(f'{\" + \".join(blocks):<18}{roc_auc_score(y,p):>8.4f}{average_precision_score(y,p):>8.4f}{brier_score_loss(y,p):>9.4f}')

p_loc = models[('local',)].predict(store, ho)
p_net = models[('local','network')].predict(store, ho)
print(f'\\nAUC lift from network evidence: +{roc_auc_score(y,p_net)-roc_auc_score(y,p_loc):.4f}')
print(f'AP  lift from network evidence: +{average_precision_score(y,p_net)-average_precision_score(y,p_loc):.4f}')
"""),
    md("""
## Answer 1: AUC. It says the moat is small.

`IDEA.md` claims +0.107 AUC. The current generator produces roughly +0.04.
The gap is not that the network model got worse -- it reproduces almost exactly
-- it is that **the local-only model is stronger than documented**. The shipped
generator leaks more persona through merchant-visible signals than the version
the pitch was written against.

Worse: the lift *shrinks* as the dataset grows, because local-only keeps
learning while local+network has already saturated.
"""),
    code("""
# the same ablation on the smaller calibrated dataset, for comparison
store100, vault100 = FeatureStore.load(DATA100K), TruthVault(DATA100K)
ho100 = store100.split('holdout'); y100 = vault100.labels(store100.payment_ids(ho100))
row = {}
for blocks in [('local',), ('local','network')]:
    m = Adjudicator(blocks).fit(store100, vault100)
    row[blocks] = roc_auc_score(y100, m.predict(store100, ho100))
print(f'100k  ({len(ho100)} holdout cases): local {row[(\"local\",)]:.4f} -> +net {row[(\"local\",\"network\")]:.4f}  lift +{row[(\"local\",\"network\")]-row[(\"local\",)]:.4f}')
print(f'300k  ({len(ho)} holdout cases): local {roc_auc_score(y,p_loc):.4f} -> +net {roc_auc_score(y,p_net):.4f}  lift +{roc_auc_score(y,p_net)-roc_auc_score(y,p_loc):.4f}')
print('\\nMore data narrows the gap. That is the honest read.')
"""),
    md("""
## Answer 2: rupees at a shared threshold. It says the moat is enormous.

This is the comparison it is tempting to make, and it is **wrong**. Holding a
single raw-probability threshold across two models does not compare the models
-- it compares how well each one's raw scores happen to be calibrated at that
particular number.
"""),
    code("""
amounts = np.array([e.amount_inr for e in ho])
print(f'{\"thr\":>6}  {\"model\":<14}{\"released\":>9}{\"prec\":>7}{\"net Rs\":>16}')
print('-'*56)
for thr in (0.05, 0.20):
    nets = {}
    for tag, p in [('local', p_loc), ('local+network', p_net)]:
        rel = p < thr
        good = y[rel] == 0
        net = amounts[rel][good].sum() - amounts[rel][~good].sum()
        nets[tag] = net
        print(f'{thr:>6.2f}  {tag:<14}{rel.sum():>9}{good.mean():>7.3f}{net:>16,.0f}')
    print(f'{\"\":>6}  {\"GAP\":<14}{\"\":>9}{\"\":>7}{nets[\"local+network\"]-nets[\"local\"]:>+16,.0f}')
"""),
    md("""
## Answer 3: rupees, each model at its own operating point. The honest one.

Give each model an isotonic calibrator and let an expected-value policy choose
its own operating point on data neither model was fitted on. Now the comparison
is between the *evidence*, which is the thing we actually wanted to measure.
"""),
    code("""
from core.policy import PolicyConfig
from core.backtest import run
from core.metrics import grade, StepUpModel, calibration_ledger, CAP_GRID
from core.report import best_cap

base, su = PolicyConfig(), StepUpModel()
print(f'{\"evidence\":<18}{\"cap\":>7}{\"released\":>10}{\"prec\":>8}{\"contribution\":>16}')
print('-'*60)
res = {}
for blocks in [('local',), ('local','network')]:
    m = models[blocks]
    cap = best_cap(calibration_ledger(store, m, base), vault, base, su)
    g = grade(run(store, m, base, 'holdout').redecide(base.for_merchant(cap)), vault, su)
    res[blocks] = g
    print(f'{\" + \".join(blocks):<18}{cap:>7.3f}{g.n_released:>10}{g.precision:>8.3f}{g.net_contribution_inr:>16,.0f}')

gain = res[('local','network')].net_contribution_inr - res[('local',)].net_contribution_inr
print(f'\\nnetwork evidence is worth Rs {gain:,.0f} ({100*gain/res[(\"local\",)].net_contribution_inr:+.1f}%)')
print(f'and it releases {res[(\"local\",\"network\")].n_released - res[(\"local\",)].n_released} more orders at HIGHER precision')
print(f'  {res[(\"local\",)].precision:.3f} -> {res[(\"local\",\"network\")].precision:.3f}')
"""),
    md("""
## What to actually say

+4-5% of net contribution, +0.04 AUC. Not 13x, not +0.107.

The defensible version of the claim is qualitative and survives the numbers
being small: network evidence lets the system release **more orders at higher
precision simultaneously**. A threshold move can never do that -- it trades one
for the other. Only new evidence buys both.

That is the sentence to put in the deck, and it is true at every operating
point measured above.
"""),
]

# ---------------------------------------------------------------------------
NOTEBOOKS["03_the_decision_system.ipynb"] = [
    md("""
# 03 - From a probability to a decision

A classifier emits `p_bad`. That is not a decision. This notebook is about the
layer that turns the number into an action, which is where most of the
interesting engineering lives.
"""),
    code(PRELUDE),
    code("""
from core.feature_store import FeatureStore
from core.truth import TruthVault
from core.model import Adjudicator
from core.policy import PolicyConfig, decide, ev_release, Action
from core.backtest import run
from core.metrics import grade, StepUpModel, frontier, ev_release_ceiling

store, vault = FeatureStore.load(DATA), TruthVault(DATA)
model = Adjudicator().fit(store, vault)
cfg, su = PolicyConfig(), StepUpModel()
print(model.card.to_json())
"""),
    md("""
## Calibration is not optional here

The policy spends `p_bad` as a price. If the model says 0.2 and the true rate
is 0.4, the policy is not slightly wrong -- it is systematically buying bad
orders. Isotonic regression fitted on the last 20% of the train window fixes
this, and the reliability curve is how you check.
"""),
    code("""
ho = store.split('holdout')
y  = vault.labels(store.payment_ids(ho))
p  = model.predict(store, ho)
raw = model.raw_uncalibrated(store, ho)

edges = np.linspace(0, 1, 11)
print(f'{\"bucket\":<14}{\"n\":>6}{\"predicted\":>12}{\"observed\":>11}{\"error\":>9}')
print('-'*55)
for b in range(10):
    m_ = (np.digitize(p, edges) - 1 == b)
    if m_.sum() < 5: continue
    print(f'{edges[b]:.1f}-{edges[b+1]:.1f}      {m_.sum():>6}{p[m_].mean():>12.3f}{y[m_].mean():>11.3f}{p[m_].mean()-y[m_].mean():>+9.3f}')
print(f'\\ncalibration error: raw {np.abs(raw.mean()-y.mean()):.4f} -> isotonic {np.abs(p.mean()-y.mean()):.4f}')
"""),
    md("""
## The expected-value rule

$$EV(\\text{release}) = (1-p)\\,m\\,A \\;-\\; p\\,(A+f) \\;-\\; c_{review}$$

You earn *margin* on a recovered sale. You lose the *entire basket* plus
overhead on a bad one. That asymmetry is the whole ballgame, and it produces a
result that surprised me when it fell out of the code.
"""),
    code("""
print('EV of releasing a Rs 1,00,000 order at various p_bad (margin 25%):\\n')
for pb in (0.00, 0.05, 0.10, 0.15, 0.20, 0.25, 0.40):
    print(f'  p_bad={pb:<6} EV = Rs {ev_release(pb, 100_000, cfg):>+10,.0f}')
print(f'\\nbreak-even at p_bad = m/(1+m) = {ev_release_ceiling(cfg):.4f}')
print('\\nThis is INDEPENDENT of order size. Contribution margin -- not risk')
print('appetite -- sets the ceiling on how much doubt a release can carry.')
for m_ in (0.15, 0.25, 0.40, 0.60):
    print(f'  margin {m_:.0%}  ->  can never release above p_bad {m_/(1+m_):.3f}')
"""),
    md("""
This is a real difference from the sweep table in `IDEA.md` 7, which runs a raw
probability threshold out to 0.50. Under an expected-value policy at a 25%
margin, thresholds above 0.20 are unreachable -- the EV term forbids them long
before the risk cap does.
"""),
    code("""
front = frontier(run(store, model, cfg, 'holdout'), vault, cfg, su)
print(f'{\"cap\":>7}{\"released\":>10}{\"prec\":>8}{\"recall\":>8}{\"recovered\":>14}{\"admitted\":>13}{\"contribution\":>15}')
print('-'*76)
for r in front:
    print(f'{r[\"cap\"]:>7.3f}{r[\"released\"]:>10}{r[\"precision\"]:>8.3f}{r[\"recall\"]:>8.3f}'
          f'{r[\"recovered\"]:>14,.0f}{r[\"admitted\"]:>13,.0f}{r[\"contribution\"]:>15,.0f}')
print('\\nnote where it stops moving.')
"""),
    md("""
## Four actions, not two

A system that only says yes or no is lying about the cases where it does not
know. There are four outcomes, and two of them are refusals.
"""),
    code("""
class Case:
    def __init__(s, amt, orders=25, tenure=900):
        s.payment_id='pay_demo'; s.amount_inr=amt
        s.network={'network_orders_prior':orders,'network_tenure_days':tenure}

print('SAME p_bad, DIFFERENT amounts -> different actions:\\n')
print(f'{\"p_bad\":>7}{\"Rs 500\":>12}{\"Rs 50,000\":>14}{\"Rs 5,00,000\":>14}')
print('-'*48)
for pb in (0.01, 0.10, 0.35, 0.60, 0.90):
    row = [decide(pb, Case(a), cfg).action.value for a in (500, 50_000, 500_000)]
    print(f'{pb:>7.2f}{row[0]:>12}{row[1]:>14}{row[2]:>14}')

print('\\n\\nTHIN FILE -- the abstention gate. Note it ignores p_bad entirely:\\n')
for pb in (0.001, 0.05, 0.50, 0.99):
    d = decide(pb, Case(500_000, orders=1, tenure=120), cfg)
    print(f'  p_bad={pb:<7} -> {d.action.value:<10} {d.reasons[0]}')
"""),
    md("""
That second table is the important one. The gate fires on **evidence quantity**,
never on model confidence. A gradient-boosted model will happily emit 0.001 on
a one-order file; that number is not knowledge, it is the prior wearing a
costume. The system refuses regardless.

## What the replay produces
"""),
    code("""
ledger = run(store, model, PolicyConfig(cap=0.02), 'holdout')
out = grade(ledger, vault, su)
print('decision mix:', ledger.counts())
print(f'\\nabstained on {out.abstention_rate:.1%} of cases  (step-up {ledger.counts()[\"STEP_UP\"]}, escalate {ledger.counts()[\"ESCALATE\"]})')
print(f'released {out.n_released} at {out.precision:.1%} precision')
print(f'recovered Rs {out.recovered_inr/1e7:.2f} cr, admitted Rs {out.fraud_admitted_inr/1e5:.2f} L')
print(f'net contribution Rs {out.net_contribution_inr/1e7:.2f} cr')
print('\\nand one sample ledger record:\\n')
print(ledger.records[7].to_json())
"""),
]

# ---------------------------------------------------------------------------
NOTEBOOKS["04_worked_cases.ipynb"] = [
    md("""
# 04 - The worked cases, re-checked

`IDEA.md` 5 tells the story through six specific payment IDs. This notebook
pulls each one out of the regenerated data and checks what the built system
actually does with it.

**There is a problem with using them in the demo, and it is found at the bottom
of this notebook.** Read to the end before building slides on these.
"""),
    code(PRELUDE),
    code("""
from core.feature_store import FeatureStore
from core.truth import TruthVault
from core.model import Adjudicator
from core.policy import PolicyConfig, decide

# the pitch cases live in the 100k baseline
store, vault = FeatureStore.load(DATA100K), TruthVault(DATA100K)
model = Adjudicator().fit(store, vault)
cfg = PolicyConfig(cap=0.05)
by_id = {e.payment_id: e for e in store.cases}

CASES = [
 ('1  the anniversary gift',      'pay_OTsB19Q7mTYMNh'),
 ('2  clean-looking stolen card', 'pay_W2hA3GA0lkTTCi'),
 ('2b trivially bad',             'pay_UEvFWPHFDVkAbU'),
 ('3  the Kolkata pharmacy order','pay_g8ZOxRE2gbxPX7'),
 ('4  genuinely ambiguous',       'pay_dlTUV3L9E44RuJ'),
 ('5  where it gets it wrong',    'pay_sipFWzV2kBhgMl'),
]
print(f'all six present in the regenerated data: {all(pid in by_id for _, pid in CASES)}')
"""),
    md("""
Grading these needs three outcomes, not two. Releasing a good order is right;
upholding a bad one is right; **abstaining is not an error** -- it is the
designed response to evidence that cannot settle the question. Only a release
that loses money, or an uphold that refuses a good customer, counts as wrong.
"""),
    code("""
IDEA_EXPECTS = {
 'pay_OTsB19Q7mTYMNh': 'OVERTURN', 'pay_W2hA3GA0lkTTCi': 'UPHOLD',
 'pay_UEvFWPHFDVkAbU': 'UPHOLD',   'pay_g8ZOxRE2gbxPX7': 'OVERTURN',
 'pay_dlTUV3L9E44RuJ': 'abstain',  'pay_sipFWzV2kBhgMl': 'OVERTURN (and lose)',
}

def verdict(action, outcome):
    good = outcome == 'clean'
    if action == 'OVERTURN':
        return 'RIGHT - released a good order' if good else 'WRONG - released a bad one'
    if action == 'UPHOLD':
        return 'WRONG - refused a good customer' if good else 'RIGHT - block stands'
    return 'ABSTAINED - not an error, but no money moved'

for label, pid in CASES:
    e, t = by_id[pid], vault.grade([pid])[0]
    p = model.predict_one(e); d = decide(p, e, cfg); n = e.network
    print(f'{label}')
    print(f'   {pid}  {e.merchant}  Rs {e.amount_inr:,.0f}  blocked for {e.meta[\"block_reason\"]}')
    print(f'   network: {n[\"network_orders_prior\"]:.0f} orders / {n[\"network_merchants_prior\"]:.0f} merchants / '
          f'{n[\"network_tenure_days\"]:.0f}d / clean {n[\"network_clean_rate\"]:.3f} / {n[\"network_disputes_prior\"]:.0f} disputes')
    print(f'   p_bad {p:.4f}  ->  {d.action.value:<9}   truth {t.true_outcome} ({t.persona})   split {t.split}')
    print(f'   {verdict(d.action.value, t.true_outcome)}')
    print(f'   IDEA.md narrates: {IDEA_EXPECTS[pid]}')
    print(f'   reasons: {\", \".join(d.reasons)}')
    print()
"""),
    md("""
### Where the built system departs from the written narrative

Three of the six do not play out the way `IDEA.md` tells it. That is worth
knowing before the story is on a slide.

- **Case 3** (the Kolkata pharmacy order) is narrated as an overturn. The model
  scores it around 0.22, and at a Rs 14,627 basket the EV term is negative, so
  it abstains instead. An 88-order, 1,132-day, spotless file *should* clear this
  and does not -- that is a genuine model miss, and the most interesting bug in
  the system right now.
- **Case 4** abstains, exactly as narrated. This one holds.
- **Case 5** is narrated as the expensive mistake, the Rs 8.4L friendly-fraud
  release. At this operating point the system abstains instead, so the demo does
  not get its cautionary tale for free. The costliest *actual* wrong releases are
  in `METRICS.md` 6 -- use one of those.
"""),
    md("""
## Case 2 is the one that earns trust

A perfect 1.000 clean rate, and the system still upholds the block. Two orders
spread across *seven different merchants* in 33 days is not a shopping
pattern -- it is reconnaissance, a stolen instrument being tested thinly and
widely before the real spend.

A model keyed on `clean_rate` releases this. A model that reads the *shape* --
orders against merchant breadth against tenure -- does not. Compare the
orders-per-merchant ratio of case 2 against case 1.
"""),
    code("""
for label, pid in CASES:
    n = by_id[pid].network
    ratio = n['network_orders_prior'] / max(n['network_merchants_prior'], 1)
    per_month = n['network_orders_prior'] / max(n['network_tenure_days']/30, 1)
    print(f'{label:<32} orders/merchant {ratio:>6.1f}   orders/month {per_month:>5.1f}   clean {n[\"network_clean_rate\"]:.3f}')
"""),
    md("""
## Case 5 belongs in the demo precisely because it is a failure

Friendly fraud is committed by real customers with real histories. The evidence
that exonerates an honest atypical buyer looks identical to the evidence
protecting a first-party abuser, because it *is* the same evidence. No
threshold removes this class of error -- the operating point only prices it.

## The problem with these six cases

Five of the six are in the **train** split.
"""),
    code("""
from collections import Counter
splits = Counter(vault.grade([pid])[0].split for _, pid in CASES)
print('split of the six pitch cases:', dict(splits))
print()
print('The model was fitted on the first 80% of train. Showing a live')
print('"RECLAIMIFY overturns this case" on a case the model trained on is')
print('circular, and a judge who asks "was this in your training set?"')
print('gets an answer you do not want to give on camera.')
"""),
    md("""
### Holdout substitutes for each narrative role

Same stories, cases the model has never seen. These are the ones to demo.
"""),
    code("""
ho = store.split('holdout')
p_ho = model.predict(store, ho)
truth = {t.payment_id: t for t in vault.grade(store.payment_ids(ho))}

def pick(pred, key, n=2):
    rows = [(e, p) for e, p in zip(ho, p_ho) if pred(e, p, truth[e.payment_id])]
    rows.sort(key=key)
    return rows[:n]

roles = [
 ('long clean file, wrongly blocked, high value (Case 1 role)',
  lambda e,p,t: t.true_outcome=='clean' and e.network['network_orders_prior']>=40
                and e.network['network_clean_rate']>=0.95 and e.amount_inr>100_000,
  lambda r: -r[0].amount_inr),
 ('clean-looking but genuinely bad (Case 2 role)',
  lambda e,p,t: t.true_outcome!='clean' and e.network['network_clean_rate']>=0.95
                and e.network['network_orders_prior']<=6,
  lambda r: -r[0].amount_inr),
 ('thin file, high value -> must abstain (Case 4 role)',
  lambda e,p,t: e.network['network_orders_prior']<3 and e.amount_inr>100_000,
  lambda r: -r[0].amount_inr),
 ('long real history, still goes bad (Case 5 role)',
  lambda e,p,t: t.true_outcome=='chargeback_friendly' and e.network['network_orders_prior']>=20,
  lambda r: -r[0].amount_inr),
]

for name, pred, key in roles:
    print(f'--- {name} ---')
    for e, p in pick(pred, key):
        t = truth[e.payment_id]; d = decide(p, e, cfg); n = e.network
        print(f'  {e.payment_id}  {e.merchant:<14} Rs {e.amount_inr:>11,.0f}  {e.meta[\"block_reason\"]:<20}')
        print(f'     {n[\"network_orders_prior\"]:.0f} orders/{n[\"network_merchants_prior\"]:.0f} merchants/'
              f'{n[\"network_tenure_days\"]:.0f}d/clean {n[\"network_clean_rate\"]:.2f}  ->  p_bad {p:.3f}  {d.action.value}   truth={t.true_outcome}')
    print()
"""),
    md("""
Swap these into the demo script and the whole run is on data the model has
never seen. It costs an afternoon of slide edits and removes the single
easiest question a judge can ask.
"""),
]


# ---------------------------------------------------------------------------
def build(execute=True):
    os.makedirs(HERE, exist_ok=True)
    written = []
    for name, cells in NOTEBOOKS.items():
        nb = new_notebook(cells=cells, metadata={
            "kernelspec": {"display_name": "Python 3", "language": "python",
                           "name": "python3"},
            "language_info": {"name": "python"},
        })
        path = os.path.join(HERE, name)
        nbf.write(nb, path)
        written.append(path)
        print(f"built {name} ({len(cells)} cells)")

    if execute:
        from nbclient import NotebookClient
        for path in written:
            nb = nbf.read(path, as_version=4)
            print(f"executing {os.path.basename(path)} ...", end=" ", flush=True)
            NotebookClient(nb, timeout=1200, kernel_name="python3",
                           resources={"metadata": {"path": HERE}}).execute()
            nbf.write(nb, path)
            print("ok")
    return written


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-exec", action="store_true")
    build(execute=not ap.parse_args().no_exec)
