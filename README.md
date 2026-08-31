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

At the shipped settings the system refuses to decide on 15.3% of cases. A
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

Layer 2, the agent: done. It writes a plain-English note for every case, runs
the verification exchange, keeps a tamper-evident log, and hands escalations to
a reviewer with a written brief. One screen on top of it, a self-contained HTML
case room that opens from disk.

The order is the argument, and it is checked rather than claimed. Run the
backtest and the agent over the same 300 cases and every score is identical to
the last digit; the only two decisions that differ are the two the agent
resolved by asking the customer a question, which the backtest has no way to do.

The language model explains and negotiates. It never decides whether money
moves. Three separate tests enforce that: the tools it can reach take no text,
the fact-extraction schema has no field for a decision, and verdicts are handed
a decision that was already made.

All six demo cases carry real, citation-checked model verdicts, recorded and
replayed off-network. The rest run on deterministic templates. Every verdict on
the screen says which it is: a badge naming the model, or one reading TEMPLATE,
NOT MODEL OUTPUT, with the split stated in the header. A screenshot of the page
cannot imply model output that is not there. The free tier is 20 requests per
day per model, so wider live coverage needs a paid key rather than any code
change.

## Results

Reviewing 1,775 blocked orders the model was not trained on, at the operating
point that actually ships:

| | |
|---|---|
| Good orders rescued | 88.2% of recoverable |
| Released, and good | 96.3% |
| Revenue recovered | Rs 7.21 cr |
| Fraud let through | Rs 51.21 L |
| Net contribution | Rs 1.29 cr |
| Refused to decide | 15.3% |

The operating point is cap 0.20, which is not tuned. It is where the expected
value of releasing stops being positive at a 25% margin. Tuning it down to 0.02
would show 98.6% precision and Rs 22.57 L of fraud instead, which is the
threshold that flatters the table. Picking that is the behaviour this project
exists to criticise, so the headline uses the EV point and leads with recall.

Every released order carries a written note explaining why, and every number in
those notes is checked against the evidence before it is shown. A note that
invents a figure is rewritten.

Four things to read alongside that table. All of them make it look worse.

**A human reviewer beats this system.** A person adjudicating every case at
87.5% accuracy reaches Rs 1.36 cr against our Rs 1.29 cr. On judgment alone a
competent reviewer wins.

What beats the reviewer is coverage. Manual review costs about Rs 150 and four
hours per case, so real queues are ranked by value and worked down until the
day runs out:

| Reviewer coverage | Cases reviewed | Recall of recoverable | Net contribution |
|---|---|---|---|
| all 1,775 | 1,775 | 88.3% | Rs 1.36 cr |
| top 3% by value | 53 | 2.4% | Rs 28.17 L |
| top 10% by value | 178 | 8.6% | Rs 69.41 L |
| **this system, all 1,775** | **1,775** | **88.2%** | **Rs 1.29 cr** |

The gap is not intelligence, it is reach. The cases a queue skips are exactly
the small ones where the customer never complains and quietly never comes back.

**The money depends on where it runs.** Everything above assumes inline at
checkout, where a released order converts at full value because the customer is
still in session. As a queue that emails people later, only some return.
Breakeven is a 28.6% return rate; below that the programme costs money to run.
`METRICS.md` section 11 writes out every term so the arithmetic can be checked
by hand.

**The rupee figures move a lot between runs.** Regenerating the world under five
random seeds and regrading all of them at this same operating point gives net
contribution anywhere from Rs 0.90 cr to Rs 1.86 cr, precision 92.0% to 98.2%,
fraud admitted Rs 8.13 L to Rs 51.21 L. Recall is the steadiest, 83.1% to 89.6%.
Quote the range.

An earlier draft of this file said precision was the stable number at 97.8% to
99.1%. That range was measured at a cap re-tuned on each seed, which absorbs
exactly the variation being measured. At a fixed operating point the variation
shows up.

**The cross-merchant advantage is modest.** About +4.6% in profit terms over a
merchant using everything it can see on its own, and +0.043 to +0.072 AUC.
Older drafts of our own documents claimed considerably more, and were wrong.
The claim that survives is about shape rather than size: network evidence
releases more orders at higher accuracy at the same time. Moving a threshold
trades one against the other; new evidence buys both.

## Does it work on a merchant it has never seen

This is pitched as a platform product, so it has to. Drop the largest merchant
out of training entirely and score only them:

| Trained on | AUC | Precision | Net contribution |
|---|---|---|---|
| everything | 0.8832 | 92.9% | Rs 99.71 L |
| everything except Aurum Jewels | 0.8824 | 89.0% | Rs 68.69 L |

Ranking transfers, pricing does not. AUC falls by 0.0008, which is nothing.
Precision at the same cap falls from 92.9% to 89.0%.

The expensive part, learning what fraud looks like across merchants, comes for
free. The cheap part, calibrating to one merchant's baskets and margins, needs
a few hundred of their own cases. That is the shape of a platform product, and
the precision drop is what makes it credible rather than a slogan.

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

| Path | Contents |
|---|---|
| `simulation/` | The whole system: decision core, agent, tests, notebooks |
| `simulation/METRICS.md` | Every number the project quotes, generated |
| `simulation/notebooks/` | Five explainers with live output, start at `00_start_here.ipynb` |
| `simulation/notes.txt` | Running log of what was built and what broke |
| `console.html` | Five-page console, also served by `run.py serve`. The agent page runs the real model. |
| `flow.html` | Browser walkthrough of the pipeline, one real case through eight stages |
| `knowledge.txt` | What to run, what the idea is, what is left to do |
| `SCRIPT.txt` | The demo script, five beats |
| `IDEA.md` | The pitch. Sections 5 and 7 regenerate from the harness. |
| `ARCHITECTURE.md` | The original plan. Read it for intent, not numbers. |
| `PULL_REQUEST.md` | Full change writeup, including what is wrong with it |

`simulation/METRICS.md` regenerates from current code and is the only document
whose numbers are current by construction. `IDEA.md` sections 5 and 7 and
`DATA_CARD.md` sections 2, 4 and 5b regenerate from the harness between markers
via `python run.py docs`, and `python -m core.docs --check` fails if either has
drifted. Everything else, this file included, is hand-written against those and
can go stale.

## Running it

```bash
cd simulation
pip install -r requirements.txt
python run.py data300k    # build the simulated world   ~31s
python run.py test        # 79 tests                    ~25s
python run.py metrics     # regenerate every number     ~60s
python run.py room        # build the case room, then open artifacts/case_room.html
python run.py serve       # build the console and serve it on http://localhost:4000
python run.py agent       # run the agent over the blocked pile
python run.py docs        # refill the generated blocks in IDEA.md/DATA_CARD.md
python run.py dry         # pre-demo check, network cut, 13 checks
python run.py seeds       # how much it moves between worlds, ~6 min
```

If you only have two minutes:

```bash
cd simulation && python run.py data300k && python run.py room
```

then open `simulation/artifacts/case_room.html`. It is one file, no server, no
network.

`python run.py serve` puts the whole system on http://localhost:4000: the
pipeline, the portfolio, the operating-point curve, the honest-metrics page,
and an agent page you can paste a transaction into. `/case` on the same server
is the case room. That agent page evaluates the 300 fitted trees in the
browser, and the build refuses to write it unless the JavaScript reproduces the
Python probability on all 1,775 holdout cases.

Nothing on either page loads from the network, so opening the files straight
off disk works too.
