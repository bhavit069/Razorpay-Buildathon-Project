# RECLAIMIFY

**A defence attorney for declined customers.**

Razorpay Buildathon, Track 02, AI Risk Manager

---

> Your fraud system has never lost a case, because nobody it convicts gets a lawyer.

---

## 1. The thesis in one paragraph

Every fraud system ever built is a prosecutor. It observes a transaction, decides it looks guilty, and blocks it. There is no appeal, no investigation, no second opinion, and, critically, **no record that anything was lost.** A blocked order leaves no row in any ledger saying "we just rejected a good customer." It leaves an absence. RECLAIMIFY is the defence: an agent that treats the decline pile as a docket, opens a case on every blocked order, gathers exculpatory evidence the merchant cannot see on its own, and overturns the wrong convictions within seconds, turning dead orders back into completed sales, while reporting exactly how much fraud it let through in the process.

---

## 2. The problem, properly stated

### 2.1 What a false decline is

A false decline is a legitimate order that a risk system rejected as fraudulent. In the Indian context this is not primarily an issuer-side card decline, the payment mix is UPI-dominant, it is **the merchant's own risk stack refusing its own customer**:

- a fraud score above threshold, so checkout is blocked;
- an RTO/return-risk score above threshold, so COD is refused on an order the customer would only place as COD;
- a manual-review hold that the customer abandons before anyone looks at it.

All three are the same loss with different labels: **revenue the merchant chose not to take, from a customer who wanted to pay.**

### 2.2 Why it is bigger than the fraud it prevents

The industry evidence is consistent in direction even where the numbers are soft:

| Claim | Figure | Source | Caveat |
|---|---|---|---|
| False declines vs fraud prevented | ~13× | Javelin (2021), widely re-cited | Vendor-aggregated, directional only |
| Global false-decline loss | ~$443B vs ~$48B ecommerce fraud | Aite-Novarica / Statista | Same |
| Share of merchant-declined orders that are good | 30 to 70% | Signifyd | Wide range for a reason |
| Average merchant revenue lost to false declines | up to 5.5% | Riskified | Self-reported by a vendor selling the fix |
| **Merchants who track their false-decline rate** | **~64%** | Corgi Labs (2026) | The important one |

**Treat every one of these as directional.** They are vendor-aggregated, several trace to a single 2021 study, and India-specific data is thin. Cite them with dates and let your own experiment carry the argument.

The last row is the one that matters most. **More than a third of merchants do not measure the single largest cost in their payments stack.** That is not negligence, it is a structural consequence of §2.3.

### 2.3 Why nobody sees it (the hidden user)

Fraud loss is *visible and violent*: a chargeback arrives, money is clawed back, there is a dispute record, a fee, an email. Somebody's quarterly number moves.

False-decline loss is *invisible and silent*:

1. The customer is blocked at checkout.
2. They do not complain, they assume their bank declined them, or that the site is broken.
3. They shop somewhere else.
4. The merchant's dashboard shows a healthy fraud rate and a clean chargeback ratio, and the fraud team gets praised for it.

The person harmed, the honest customer who was refused, **is a user of the system who appears in none of its data.** Every incentive in the stack points one way: fraud losses are measured, so they get optimised; decline losses are not, so they get ignored. Blocking more always looks like winning.

This asymmetry is the entire opportunity.

### 2.4 Why nobody has fixed it

Three reasons, and all three have expired:

**Re-adjudication needed a human.** Manual review exists, but it is slow, expensive, and rationed to high-value B2B orders. Nobody manually reviews a ₹1,400 consumer decline, the review costs more than the order. Agents made per-case investigation approximately free.

**The evidence wasn't available.** To exonerate a customer you need evidence about that customer, and a single merchant only holds its own thin slice. A first-time buyer at Voltcart is, by construction, a stranger to Voltcart. See §4, this is the crux.

**Nobody owned the metric.** The fraud team is measured on fraud losses. Releasing declines *increases* their number while the benefit lands in a revenue line they don't own. No individual has ever been rewarded for reducing false declines.

---

## 3. What exists, and where it stops

| Layer | What it does | Where it stops |
|---|---|---|
| Merchant risk rules | Score and block at checkout | At the decline. No appeal path. |
| RTO / return-risk scoring (e.g. Thirdwatch-class) | Refuse COD on risky orders | Treats a wrong refusal as free. |
| Manual review queues | Human adjudication | Rationed to high-value cases; hours of latency. |
| Dispute responders (incl. Agent Studio's) | Fight chargebacks *after* they land | Operates only on orders that were **allowed**. |
| Fraud orchestration platforms | Route between risk vendors | Still optimising the block decision, not reviewing it. |

**The shared assumption across all of them: the cost of a wrong block is zero.**

RECLAIMIFY does not compete with any of these. It sits *downstream of the decline*, on a pile every one of them creates and none of them revisits. Its relationship to a Thirdwatch-class product is the relationship between a trial court and an appeals court: the first decides, the second re-examines with more evidence and a different standard.

---

## 4. The unfair advantage: evidence only a platform holds

This is the load-bearing idea. Everything else is engineering.

**The customer a merchant just declined as a stranger is not a stranger to the platform.**

A merchant scoring an order sees: *this customer, at this merchant.* First order here → thin file → high risk. Correct, given what it can see.

A payment platform sees: *this identity, across every merchant on the rails.* Eight hundred orders over three years, forty merchants, zero disputes, consistent device, consistent pincode, stable instrument.

That is exculpatory evidence, and **a single merchant cannot obtain it at any price.** It answers the "why hasn't this existed?" question and the "why is this a platform product, not a plugin?" question in one move.

### 4.1 It is measurable, and it was measured

Claiming a moat is easy. This one was tested on the held-out set:

| Feature set | Holdout AUC | Holdout AP |
|---|---|---|
| Merchant-local features only | 0.813 | 0.579 |
| **Local + cross-merchant network** | **0.920** | **0.823** |
| Network only | 0.743 | 0.644 |

**Network lift: +0.107 AUC, +0.244 average precision.** Robust across a 7× sweep of block-rate regimes (lift never below +0.097). See `DATA_CARD.md` §3.

Read that as: *a merchant using everything it can possibly see does measurably worse at identifying its own mistakes than the platform does.* That is not a marketing claim. It is a number on a held-out set.

### 4.2 The naive version of this idea, and why it fails

"Long clean history ⇒ safe" is the obvious heuristic, and it is wrong. Sophisticated fraud **farms** clean history precisely to defeat it: age the account, place small clean orders, then bust out.

The dataset models this explicitly, 42% of the fraudster population are bust-outs with clean seeded files, for one reason: **if the demo only shows RECLAIMIFY releasing obviously-good customers, it proves nothing.** The system has to be shown *refusing* to release someone whose record looks spotless. §5, Case 2.

---

## 5. Worked examples

Every case below is a real record from the generated dataset, with its real
payment id, real feature values, and the true outcome from the answer key.
Nothing here is illustrative fiction, and nothing here is typed by hand.

<!-- GENERATED: idea-portfolio -->
Portfolio: 300,000 orders, 8 merchants, 365 days. The risk stack blocked **8,265** of them (2.76%). Of those, **6,346 were good customers**, Rs 44.25 cr of revenue refused, against Rs 10.81 cr of genuine fraud correctly stopped. A ratio of 4.1 to 1.

The temporal holdout, which is what every number in `METRICS.md` is measured on, is the last 61 days: 1,775 blocked orders the model never trained on.
<!-- END GENERATED -->

<!-- GENERATED: idea-cases -->
Selected by predicate, not by payment id, and only from the holdout. An earlier revision named six ids by hand; five of them sat in the train split, so the worked examples were rows the model had been fitted on. Each case below is the strongest holdout match for a role described by shape, so the list survives a reseed. Regenerate with `python run.py docs`.

### Case 1: A long clean file released for a large amount

`pay_5E72cODtQrmZkn` at Aurum Jewels, **Rs 5.16 L**, upi, blocked for `amount_anomaly`.

| | |
|---|---|
| What the merchant could see | new device, basket 4.56 sd above this merchant's norm, pincode return index 1.15 |
| Merchant risk score | 0.509 against a threshold of 0.38 |
| What only the network can see | 75 prior orders across 1 merchant, 1534 days of tenure, clean rate 0.960, 1 prior dispute, 2 prior returns |
| Probability the block was correct | **0.003** |
| Action | **OVERTURN** |
| True outcome | `clean` (persona `legit_stable`) |

The core case. Every local signal fires, the network file explains all of them, and the money is real.

### Case 2: A spotless record the system still refuses

`pay_0T6ICR8cppsGGD` at PharmaNow, **Rs 1,195**, cod, blocked for `device_reputation`.

| | |
|---|---|
| What the merchant could see | new device, first order at this merchant, ordered at night, cash on delivery, disposable email domain, international, pincode return index 1.20 |
| Merchant risk score | 0.913 against a threshold of 0.66 |
| What only the network can see | 1 prior order across 7 merchants, 344 days of tenure, clean rate 1.000, 0 prior disputes, 0 prior returns |
| Probability the block was correct | **0.750** |
| Action | **UPHOLD** |
| True outcome | `rto_return` (persona `abuser`) |

Separates this from a naive rehabilitator. The clean rate is perfect and the block still stands, because a handful of orders spread across many merchants in short tenure is a shape, not a record. A model keyed on clean rate alone releases this one.

### Case 3: An easy refusal

`pay_pB3gMaABohjGbB` at Aurum Jewels, **Rs 4.61 L**, wallet, blocked for `thin_file_high_value`.

| | |
|---|---|
| What the merchant could see | new device, first order at this merchant, ordered at night, basket 4.36 sd above this merchant's norm, pincode return index 1.35 |
| Merchant risk score | 0.870 against a threshold of 0.38 |
| What only the network can see | 6 prior orders across 4 merchants, 375 days of tenure, clean rate 0.000, 2 prior disputes, 0 prior returns |
| Probability the block was correct | **0.962** |
| Action | **UPHOLD** |
| True outcome | `fraud_undisputed` (persona `fraudster`) |

Not every case is hard, and the system should say so quickly.

### Case 4: Too thin to judge

`pay_5sr9UbuKWw8GCM` at Aurum Jewels, **Rs 2.93 L**, cod, blocked for `amount_anomaly`.

| | |
|---|---|
| What the merchant could see | new device, shipping address differs from billing, first order at this merchant, ordered at night, cash on delivery, basket 3.53 sd above this merchant's norm, pincode return index 1.25 |
| Merchant risk score | 0.776 against a threshold of 0.38 |
| What only the network can see | no prior orders anywhere on the network, 335 days of tenure |
| Probability the block was correct | **0.204** |
| Action | **STEP_UP** |
| True outcome | `chargeback_friendly` (persona `friendly_fraudster`) |

No exculpatory evidence exists, so the honest answer is that there is no answer. Abstention is a reported outcome, not a hidden fallback.

### Case 5: The most expensive wrong release

`pay_cHqPoyD6hCHji7` at Aurum Jewels, **Rs 2.12 L**, cod, blocked for `address_mismatch`.

| | |
|---|---|
| What the merchant could see | new device, shipping address differs from billing, ordered at night, cash on delivery, basket 2.94 sd above this merchant's norm |
| Merchant risk score | 0.384 against a threshold of 0.38 |
| What only the network can see | 47 prior orders across 2 merchants, 1453 days of tenure, clean rate 0.979, 0 prior disputes, 1 prior return |
| Probability the block was correct | **0.098** |
| Action | **OVERTURN** |
| True outcome | `rto_return` (persona `legit_stable`) |

First-party misuse. A real customer with a real history who disputes anyway, where the exonerating evidence is the same evidence. No threshold removes this class; the operating point prices it.

### Case 6: Refused partly for where they live

`pay_YUcHCf0wIe45Yo` at HomeHaul, **Rs 21,711**, wallet, blocked for `device_reputation`.

| | |
|---|---|
| What the merchant could see | new device, a prior return at this merchant, basket 1.64 sd above this merchant's norm, pincode return index 1.45 |
| Merchant risk score | 0.606 against a threshold of 0.5 |
| What only the network can see | 25 prior orders across 1 merchant, 608 days of tenure, clean rate 0.880, 1 prior dispute, 2 prior returns |
| Probability the block was correct | **0.003** |
| Action | **OVERTURN** |
| True outcome | `clean` (persona `legit_stable`) |

High-RTO pincodes are Tier-2 and Tier-3 India. A stack tuned on regional return rates withdraws from the fastest-growing market.
<!-- END GENERATED -->

---

## 6. How it works

### 6.1 The pipeline

```
BLOCKED ORDER
     │
     ├─ 1. DOCKET      open a case; pull the merchant's stated block reason
     │
     ├─ 2. EVIDENCE    assemble point-in-time cross-merchant file:
     │                 tenure, order count, merchant breadth, completion rate,
     │                 dispute/RTO history, instrument and device consistency
     │
     ├─ 3. ADJUDICATE  P(this block was correct | local + network evidence)
     │                 gradient-boosted model, calibrated, held-out validated
     │
     ├─ 4. TRIAGE      confident-good  → OVERTURN, release to checkout
     │                 confident-bad   → UPHOLD, close case
     │                 ambiguous       → STEP-UP or ESCALATE
     │
     ├─ 5. STEP-UP     for mid-confidence high-value: a real-time verification
     │                 exchange that generates *new* evidence rather than
     │                 re-weighing old evidence
     │
     └─ 6. VERDICT     written rationale, logged, auditable, per case
```

### 6.2 Where the ML is, and where the LLM is

The division is deliberate and should be stated out loud in the pitch, because most submissions get it backwards.

**Classical ML owns every decision that moves money.** The adjudication model is a calibrated gradient-boosted classifier over local + network features, validated on a temporal holdout. Threshold selection is an explicit expected-value optimisation over recovery, fraud admitted, and review cost. Abstention is a confidence-band rule.

**The LLM owns language, and only language.** Three legitimate jobs: writing the per-case verdict in plain English ("released: 1,027-day file, 78 orders, zero disputes; the flagged signals were a gift-shipping address and an off-hours order, both consistent with this customer's prior behaviour"); conducting the step-up verification exchange; and answering portfolio questions from the merchant by calling the model as a tool.

> **The LLM explains and negotiates. It never decides whether money moves.**

Say that sentence in the video. Almost no student submission will carry it, and every judge who ships ML will register it.

### 6.3 The step-up loop

For mid-confidence, high-value cases, re-weighing existing evidence is a dead end, the evidence is genuinely insufficient. The correct move is to **generate new evidence**: a short verification exchange (confirm a detail only the true account holder knows, confirm the delivery address, offer prepaid instead of COD).

This matters because it converts Case-4-type abstentions into decisions without guessing, and because offering prepaid on a refused-COD order is a *recovery mechanism that costs nothing and carries no fraud risk*, the merchant's RTO exposure disappears entirely if the customer pays up front.

---

## 7. How impact gets measured

Ground truth here is **outcome-based, not label-based**. The dataset records what each blocked order *would have done* if allowed. RECLAIMIFY is graded against that counterfactual, not against whether it agrees with a label someone wrote.

**Primary metrics**

| Metric | Why |
|---|---|
| **Overturn precision** | Of orders released, share that completed cleanly |
| **Recall against recoverable** | Of genuinely good blocked orders, share rescued |
| **₹ recovered** | The revenue line |
| **₹ fraud admitted** | The number that hurts. Always reported beside recovery. |
| **Net ₹** | Recovery − fraud admitted − review cost |
| **Abstention rate** | Cases refused, with reasons |
| **Latency per case** | Whether it is deployable |

**Baselines.** three, not one: leave the pile alone (status quo, ₹0 recovered), release everything (upper bound on recovery, unacceptable fraud), and merchant-local model only (isolates the network moat).

**The operating-point curve** is the centrepiece artifact.

<!-- GENERATED: idea-frontier -->
Measured on the holdout. Fraud admitted is printed beside recovery on every row, which is the only way this table is honest.

| cap | Released | Precision | Recall | Recovered | Fraud admitted | Net contribution |
|---|---|---|---|---|---|---|
| 0.005 | 1124 | 98.5% | 77.1% | Rs 6.95 cr | Rs 22.57 L | Rs 1.51 cr |
| 0.01 | 1124 | 98.5% | 77.1% | Rs 6.95 cr | Rs 22.57 L | Rs 1.51 cr |
| 0.02 | 1181 | 98.6% | 81.1% | Rs 7.04 cr | Rs 22.57 L | Rs 1.53 cr |
| 0.03 | 1196 | 98.6% | 82.2% | Rs 7.08 cr | Rs 22.57 L | Rs 1.54 cr |
| 0.05 | 1213 | 98.4% | 83.2% | Rs 7.11 cr | Rs 23.23 L | Rs 1.54 cr |
| 0.075 | 1245 | 98.2% | 85.2% | Rs 7.14 cr | Rs 25.30 L | Rs 1.53 cr |
| 0.1 | 1303 | 96.7% | 87.8% | Rs 7.20 cr | Rs 48.33 L | Rs 1.31 cr |
| 0.125 | 1303 | 96.7% | 87.8% | Rs 7.20 cr | Rs 48.33 L | Rs 1.31 cr |
| 0.15 | 1314 | 96.3% | 88.2% | Rs 7.21 cr | Rs 51.21 L | Rs 1.29 cr |
| 0.175 | 1314 | 96.3% | 88.2% | Rs 7.21 cr | Rs 51.21 L | Rs 1.29 cr |
| 0.2 **(shipped)** | 1314 | 96.3% | 88.2% | Rs 7.21 cr | Rs 51.21 L | Rs 1.29 cr |

The shipped operating point is cap 0.2, and it is not tuned. Releasing is expected-value positive only below `m/(1+m)`, which at a 25% margin is 0.2. Choosing a lower cap buys a better-looking precision column at the cost of recall, which is the behaviour this project exists to criticise.
<!-- END GENERATED -->

It is a choice, not a result. A jeweller with lakh-rupee baskets and a
bookseller with five-hundred-rupee ones do not belong at the same point.
Presenting the curve rather than a single number is the argument that the
problem was understood rather than merely solved.

### What it actually scored

<!-- GENERATED: idea-results -->
On 1,775 blocked orders the model never trained on, at cap 0.2:

| Metric | Value | Across 5 generated worlds |
|---|---|---|
| **Recall of recoverable** | **88.2%** | 83.1% to 89.6% |
| Overturn precision | 96.3% | 92.0% to 98.2% |
| Revenue recovered | Rs 7.21 cr | - |
| **Fraud admitted** | **Rs 51.21 L** | Rs 8.13 L to Rs 51.21 L |
| Net contribution | Rs 1.29 cr | Rs 89.95 L to Rs 1.86 cr |
| Abstention rate | 15.3% | - |

Read the right-hand column before quoting the middle one. Recall is the steady quantity. Precision moves 6.2 points between worlds, and money moves about 2.1x. Each metric depends on something different: recall on how well the model ranks, precision on where the threshold lands in a particular world, money on the value distribution of that world's blocked pile.

Deployment assumption: inline at checkout. As a queue review the breakeven is a 28.6% customer return rate. `METRICS.md` 11 has the arithmetic term by term.

Baselines that matter, both of them:

| | Recall of recoverable | Net contribution |
|---|---|---|
| Human reviewer, all 1,775 cases | 88.3% | Rs 1.36 cr |
| Human reviewer, top 3% by value | 2.4% | Rs 28.17 L |
| Human reviewer, top 10% by value | 8.6% | Rs 69.41 L |
| **This system, all 1,775 cases** | **88.2%** | **Rs 1.29 cr** |

A reviewer beats this system case for case and it is left in the table because it is true. Nobody can afford to run one across the whole pile, so in practice most cases are never reviewed at all.
<!-- END GENERATED -->

---

## 8. The dataset

Full documentation in `DATA_CARD.md`. The one property that matters:

**False positives are never injected. They emerge.**

Two stages run independently. Stage 1 builds the true world, latent personas, true counterfactual outcomes. Stage 2 runs a merchant scorecard that **never sees persona or outcome**, only observable signals. Because honest-atypical customers emit the same observable signals as fraudsters, the scorecard blocks some of them. That mismatch *is* the false-positive population. No parameter in the repo sets an FP rate.

Calibration against public anchors: block rate **2.8%** (published: ~2.7%), FP share of blocked pile **76%** (published range: 30 to 70%, we sit slightly above, stated not hidden), value ratio **3.8×** (published headline: ~13×, we are deliberately more conservative).

Three anti-circularity measures: bust-out fraudsters who farm clean files; per-customer history noise so honest people accumulate disputes for boring reasons; and deliberately leaky fraud tells. An early version of this generator scored **AUC 0.93 on merchant-local features alone.** too good, meaning persona was leaking through observables and the task was fake-easy. Compressing persona signatures brought it to a realistic 0.81. That iteration is documented because the ability to recognise a suspiciously good number is itself the skill being demonstrated.

---

## 9. Why this fits Track 02 better than the obvious ideas

The brief asks for "a working **detector, verifier or auto-responder** for one class of loss." Everyone will build the first noun. RECLAIMIFY is the **verifier.** and the only one of the three that operates on the merchant's *own errors*.

The bar is "honest metrics **including false-positive cost**." For every other submission, FP cost is a compliance slide bolted on at the end. **For RECLAIMIFY it is the operating currency.** The entire thesis is that the industry systematically under-measures it. You are not clearing the bar; you are arguing the bar should have been the whole exam.

"Strictly defense-only", RECLAIMIFY never attacks, probes, or profiles offensively. It reviews the merchant's own rejections. Nothing it produces has offensive capability.

**The wrinkle, stated first:** overturning declines necessarily admits some fraud. Say it before a judge does, and make it the thesis, RECLAIMIFY is not free money, it is the claim that the industry sits at the wrong point on a trade curve because one side of it was never measured. Reporting your own damage number is what the brief is actually asking for.

---

## 10. Honest risks

| Risk | Response |
|---|---|
| "This is a revenue product, not a risk product" | False declines are a loss *caused by* the risk stack. Fixing your own errors is risk management. Frame in the first 30 seconds. |
| "Your FP ratio is convenient" | It emerged rather than being set; the sweep in `DATA_CARD.md` §3 shows conclusions hold across a 7× range. Also state the FP share sits slightly above the published band. |
| "Razorpay already has risk review" | Manual review is rationed to high-value cases and takes hours. This is every case, in seconds, with evidence manual review cannot access. |
| "Synthetic ground truth proves nothing" | Correct, partially. Calibration and sensitivity reduce circularity; they don't eliminate it. Claim robustness to chosen parameters, not fidelity to reality. |
| "Cross-merchant data has privacy implications" | Real and worth naming. The features are aggregate behavioural counts, not shared PII, and Razorpay already holds this data. Do not hand-wave it. |
| Small holdout | ~620 blocked rows. Report confidence intervals; regenerate larger. |

---

## 11. Scope for one week

**Build fully:** the adjudication model with held-out precision/recall and the calibrated operating-point curve; the evidence assembly layer with point-in-time correctness; the abstention rule; the written-verdict generator.

**Build as one worked path:** the step-up verification loop, a single live case in the demo, not a general system.

**Build as supporting views:** the portfolio finding (which block reasons produce the most wrongful declines; which pincodes are being systematically refused).

**Cut entirely:** real-time integration, UI polish beyond one live case walkthrough, and any attempt to improve the *original* decline decision. RECLAIMIFY reviews; it does not replace the risk stack. That boundary is also the anti-collision argument against existing Razorpay tooling, keep it sharp.

**Demo arc (5 min):** the invisible loss and the 64%-don't-measure-it stat (45s) → Case 1, ₹6.7L released live with its written verdict (90s) → Case 2, the clean-looking stolen card upheld, proving the system isn't naive (60s) → Case 4, the abstention (30s) → the operating-point curve and Case 5, the ₹8.4L mistake, explained (75s).

---

## 12. The sentence that should survive everything else

> **Nobody has ever been promoted for the customers they didn't wrongly block.**

That is why this loss is invisible, why it is enormous, and why it needs an agent rather than a dashboard.
