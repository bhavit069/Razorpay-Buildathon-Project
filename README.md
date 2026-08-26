# Reviewing declined orders

Razorpay Buildathon, Track 02.

The name in the older docs is a placeholder. Ignore it.

## What this is

A merchant's fraud system blocks an order. Nothing ever looks at that decision
again. This reviews the blocked pile and decides which blocks were wrong.

## The problem

Someone tries to buy something. The shop's fraud software scores the order as
suspicious and blocks it.

They are not a fraudster. They bought a phone last week so the device is new.
Or they're sending a gift, so the delivery address doesn't match billing. Or
they shop at 1am. Or they live in Patna rather than Bengaluru.

What happens next is nothing. The customer assumes their bank declined them and
buys elsewhere. No complaint, no dispute, no refund. Nothing is written down
anywhere saying a good customer was refused.

Compare that to fraud. A chargeback arrives, money is clawed back, there's a
fee and a dispute record. Someone's number moves.

One side of the trade is measured and the other isn't, so blocking more always
looks like an improvement. Roughly a third of merchants don't track their
false-decline rate, which is less negligence than an absence of anything to
track.

## What it does

Opens a case on each blocked order, pulls evidence, and picks one of four
actions.

```
blocked order
   -> gather evidence: what does this customer look like across all merchants?
   -> score: how likely is it that this block was correct?
   -> decide: release / uphold / ask a question / send to a human
```

Release and uphold are the easy two. The other two matter more:

- **Ask a question.** Not enough evidence to be sure, enough money to be worth
  a short verification exchange. This generates new evidence instead of
  re-arguing the old evidence.
- **Send to a human.** Not enough evidence and no question would settle it.

At the default settings the system refuses to decide about 17% of cases. A
system that always answers is claiming to know things it doesn't.

## Why a payment platform

A merchant scoring a first-time order sees one thing: this person, at this shop,
never seen before. Thin file, high risk. That inference is correct given what
it can see.

A payment platform sees the same identity across every merchant on the rails.
Eight hundred orders, three years, forty merchants, no disputes, same device.

A single merchant cannot buy that.

### The naive version doesn't work

"Long clean history means safe" is the obvious rule, and fraudsters beat it
deliberately by farming a record: age the account, place small honest orders
across several shops, then cash out.

So the test isn't whether the system releases obviously-good customers. It's
whether it refuses someone whose record looks perfect. There's a case in the
data with a 1.000 clean rate that it blocks, because two orders across seven
merchants in 33 days isn't shopping, it's reconnaissance.

## The tradeoff

Releasing wrongly-blocked orders lets some fraud through. There is no version
of this that doesn't.

So every result reports money recovered and money lost side by side. There is
no table in this project showing one without the other.

The clearest illustration: releasing *every* blocked order recovers more gross
revenue than any other strategy and still loses money, because you earn margin
on the good orders and lose the whole basket on the bad ones. A recovery figure
quoted on its own means nothing.

## What's built

Two layers, in order.

Layer 1, the decision system: done. A calibrated model plus an economic
decision rule, backtested against a year of simulated traffic with the answer
key locked behind a test suite. No language model in it. Deterministic.

Layer 2, the agent: not started. Writes verdicts in English, runs the
verification conversation, serves a dashboard.

The order is the argument. The language model explains and negotiates; it never
decides whether money moves. Every decision the agent will make is one the
backtest already graded.

## Results

Reviewing 1,775 blocked orders the model was not trained on:

| | |
|---|---|
| Released | 1,181 of 1,775 |
| Of those, good | 98.6% |
| Good orders rescued | 81% of recoverable |
| Revenue recovered | ₹7.04 cr |
| Fraud let through | ₹22.57 L |
| Refused to decide | 17% |

Two things to read alongside that table.

**The rupee figures move a lot between runs.** Regenerating the world under
five different random seeds gives net contribution anywhere from ₹1.15 cr to
₹1.87 cr. Quote the range. Precision is the stable number: 97.8% to 99.1%
across the same five.

**The cross-merchant advantage is modest.** About +5% in profit terms over a
merchant using everything it can see on its own, and +0.04 to +0.07 AUC. Older
drafts of our own documents claimed considerably more, and were wrong. The
claim that survives is about shape rather than size: network evidence releases
more orders at higher accuracy at the same time. Moving a threshold trades one
against the other; new evidence buys both.

## Caveats

The data is synthetic. We wrote the world, so we wrote the answers. Calibrating
against published industry figures and sweeping the parameters reduces the
circularity but does not remove it. The claim is that the conclusions survive
the assumptions we chose, not that this is what reality looks like.

The test set is small, about 1,775 cases, and the confidence intervals are
wide. They're published rather than omitted. They also only cover sampling
error inside one generated world, which is narrower than the variation between
worlds.

77% of the holdout's value sits with one merchant, so the rupee headline is
mostly a measurement of that merchant's blocked pile.

Cross-merchant data has real privacy implications. The features are aggregate
behavioural counts rather than shared personal information, and a payment
processor already holds this data. That's an argument, not a dismissal.

This doesn't replace a fraud system. It reviews one, the way an appeals court
re-examines a trial court's decision with more evidence and a different
standard.

## Layout

| Folder | Contents |
|---|---|
| `development/` | Current work: decision system, tests, notebooks |
| `development/notebooks/` | Five explainers with live output, start at `00_start_here.ipynb` |
| `development/METRICS.md` | Every number the project quotes, generated |
| `development/notes.txt` | Running log of what was built and what broke |
| `simulation/` | Original data generator, archived |
| `IDEA.md`, `ARCHITECTURE.md` | Original plan. Numbers in these are stale. |
| `pr/` | This file, the change writeup, and a code snapshot |

`IDEA.md` and `DATA_CARD.md` quote figures from an older version of the
generator. Don't quote them. `development/METRICS.md` regenerates from current
code.

## Running it

```bash
cd development
python run.py data300k    # build the simulated world   ~31s
python run.py test        # 33 tests                     ~4s
python run.py metrics     # regenerate every number     ~60s
python run.py seeds       # how much it moves between worlds
```
