# RECLAIMIFY: synthetic dataset card

**Generated:** 2026-08-24 · **Seed:** 42 · **Rows:** 100,000 payments · **Window:** 365 days ending 2026-08-01

This dataset supports a single question: **of the orders a merchant's risk stack refused, which were actually good customers?**

---

## 1. The design decision that matters

Most synthetic fraud datasets are circular: the author labels some rows "fraud," injects features that mark them, then trains a model that rediscovers the labels. The reported precision is a measurement of the author's own imagination.

This generator avoids that in one specific way: **false positives are never injected. They emerge.**

Two stages run independently:

**Stage 1, the true world.** Customers carry a latent `persona` and every order carries a true counterfactual outcome: what *would* have happened if the order were allowed through (`clean`, `chargeback_fraud`, `fraud_undisputed`, `chargeback_friendly`, `rto_return`).

**Stage 2, the risk stack.** A merchant scorecard scores each order and blocks above a threshold. **It never sees `persona` or the true outcome.** It sees only observable signals: device newness, device fanout, address mismatch, velocity, basket anomaly, pincode RTO propensity, hour, thin-file status, email domain, prior RTO with this merchant.

Because honest-but-atypical customers emit the *same observable signals* as fraudsters (a new device, a shipping address that isn't the billing address, an unusually large basket, an odd hour), the scorecard blocks some of them. **That mismatch is the false-positive population.** No parameter anywhere in this repo sets a false-positive rate.

Three further choices exist purely to stop the problem being fake-easy:

- **Bust-out fraudsters** (42% of the fraudster persona) deliberately farm a clean network file before cashing out. Without them, "long clean history ⇒ safe" would be a free giveaway.
- **Per-customer history noise** (`history_noise`, lognormal σ=1.15). Honest people accumulate disputes for boring reasons: a late delivery, a genuinely faulty item. This stops network history from being a clean persona proxy.
- **Signature overlap.** Disposable email is used by 9% of honest customers and only 34% of bad ones; honest households share devices (1 in 14 legit customers). Every "fraud tell" is deliberately leaky, because in reality they are.

**Fraudsters are never labelled `clean`.** A stolen instrument that escapes dispute is `fraud_undisputed`, still a bad outcome. Labelling it clean would train the model to release stolen cards.

---

## 2. Calibration

<!-- GENERATED: datacard-calibration -->
Measured on the current dataset, not typed in.

| Property | This dataset | Public anchor | Source (dated) |
|---|---|---|---|
| Orders blocked for risk | **2.76%** | ~2.7% of US domestic orders declined for fraud concerns (Q3 2023) | ClearSale, retrieved 2026-08-24 |
| Share of blocked pile that was good | **76.8%** | 30-70% of merchant-declined orders estimated good | Signifyd, via 2026 playbooks |
| Value wrongly blocked / value correctly blocked | **4.09x** | False declines around 13x fraud prevented | Javelin (2021), widely re-cited |
| Merchants tracking their false-decline rate | n/a | ~64% | Corgi Labs, 2026-07 |

Our false-positive share (76.8%) sits above the published 30-70% band, and our value ratio (4.09x) is deliberately more conservative than the 13x headline. Both are stated rather than hidden. The public anchors are vendor-aggregated, several trace back to a single 2021 Javelin study, and India-specific data is thin, so treat them as order-of-magnitude context.
<!-- END GENERATED -->

India-specific modelling choices: UPI is the dominant method (62% of non-COD), COD is 34% of orders with a base RTO rate of 17% modulated by regional propensity (Tier-2/3 pincodes carry higher RTO), and `error_source` uses Razorpay's documented enum.

---

## 3. Sensitivity analysis

The headline claim is that **cross-merchant network evidence beats merchant-local evidence**. It has to survive parameter perturbation or it's an artifact. Sweeping the scorecard intercept across a 7× range of block rates:

| Intercept | Block rate | FP share | Local AUC | +Network AUC | **Lift** |
|---|---|---|---|---|---|
| −3.5 | 9.19% | 79.4% | 0.7891 | 0.8970 | **+0.1079** |
| −4.4 | 4.01% | 73.9% | 0.8391 | 0.9362 | **+0.0971** |
| −4.9 *(default)* | 2.40% | 70.8% | 0.8130 | 0.9204 | **+0.1074** |
| −5.4 | 1.35% | 66.5% | 0.7549 | 0.9204 | **+0.1655** |

The lift never drops below +0.097 AUC. Average precision improves from ~0.58 to ~0.82 at default settings.

Reproduce: `python generate.py --intercept -4.4 --out /tmp/x && python validate_signal.py /tmp/x`

---

## 4. Files

<!-- GENERATED: datacard-files -->
| File | Rows | Contents |
|---|---|---|
| `payments.jsonl.gz` | 300,000 | Razorpay-shaped payment entities |
| `orders.jsonl.gz` | 300,000 | Order entities |
| `customers.jsonl.gz` | 36,000 | Customer entities, **`persona` stripped** |
| `risk_decisions.jsonl.gz` | 300,000 | Scorecard decision plus point-in-time and network features |
| `appeal_queue.csv` | 8,265 | Blocked orders only, what RECLAIMIFY adjudicates |
| `disputes.jsonl` | 7,298 | Realised chargebacks (allowed orders only) |
| `refunds.jsonl` | 17,349 | Realised RTO refunds (allowed orders only) |
| `ground_truth.jsonl.gz` | 300,000 | **THE ANSWER KEY.** Reachable only through `core/truth.py`. |

Split chronologically: 249,679 train rows and 50,321 holdout rows, of which 1,775 were blocked and form the appeal queue every metric is measured on.
<!-- END GENERATED -->

Entity shapes mirror Razorpay's documented API: `pay_`/`order_`/`cust_` + 14 alphanumerics, **amounts in paise**, `created_at` UNIX seconds, `acquirer_data.rrn` for UPI/card and `bank_transaction_id` for netbanking, and the documented `error_source` enum (`customer`, `business`, `internal`, `gateway`, `issuer_bank`).

**One deliberate deviation, flagged in the data:** COD appears as `method: "cod"` with `_synthetic_extension: "cod_modelled_as_method"`. Razorpay has no such payment-method enum value. COD lives at the order level in Magic Checkout. It carries no gateway fee. Flagged so nobody mistakes it for a real API field.

---

## 5. Point-in-time correctness

Every feature at decision time uses only events that had already occurred. Running state (`n_orders`, `n_clean`, `n_disputes`, device sightings, 24h velocity) advances *after* each decision is recorded. There is no forward leakage.

Customers are seeded with pre-window network history proportional to tenure, because a two-year-old identity should already carry a file on day 1. Without this, early orders would all look thin-file and the platform advantage would be *understated*.

**Split is temporal, not random:** the final 61 days are `holdout` (16,756 orders, 615 blocked). A random split would leak customer identity across the boundary.

---

## 5b. Outcome and false-positive mix

<!-- GENERATED: datacard-outcomes -->
What each order *would have done* if allowed through.

| True outcome | Orders | Share |
|---|---|---|
| `clean` | 269,790 | 89.93% |
| `rto_return` | 17,997 | 6.00% |
| `chargeback_friendly` | 5,361 | 1.79% |
| `fraud_undisputed` | 3,637 | 1.21% |
| `chargeback_fraud` | 3,215 | 1.07% |

False positives are not injected, they emerge. Which personas end up in the blocked pile despite being good:

| Persona | Wrongly blocked | Share of the mistakes |
|---|---|---|
| `legit_stable` | 2,838 | 44.7% |
| `legit_new` | 1,733 | 27.3% |
| `legit_atypical` | 1,331 | 21.0% |
| `friendly_fraudster` | 243 | 3.8% |
| `abuser` | 201 | 3.2% |

`friendly_fraudster` and `abuser` appear here because on a given order they behaved, which is exactly why the class is hard: the evidence that exonerates an honest atypical buyer is the same evidence.
<!-- END GENERATED -->

---

## 6. Known limitations, state these before a judge does

1. **Ground truth is authored.** Calibration to public figures and sensitivity analysis reduce circularity; they do not eliminate it. The honest claim is "conclusions are robust to the parameters we chose," not "this is what reality looks like."
2. **The holdout appeal queue is small** (615 rows). Confidence intervals on holdout precision are wide. Report them. Generate at `--n 300000` for tighter bounds.
3. **The scorecard is a plausible fiction.** Real merchant stacks use hundreds of features and vendor models. Ours uses twelve. It is *directionally* realistic, not a replica.
4. **`network_clean_rate` dominates feature importance (~0.49).** Partly real signal, partly residual construction. Treat single-feature importance with suspicion and report ablations.
5. **No adversarial adaptation.** Fraudsters here don't learn that RECLAIMIFY exists and probe it. A real deployment would face exactly that.
6. **Indian regulatory specifics are not modelled**, no AFA thresholds, tokenisation, or UPI mandate mechanics. Not needed for this loss class, but don't claim otherwise.

---

## 7. Usage

```bash
python generate.py --n 100000 --seed 42 --out ./data
python validate_signal.py ./data

# sensitivity
python generate.py --intercept -4.4 --out /tmp/paranoid
python generate.py --fp-sensitivity 0.9 --out /tmp/tighter
```

**Training protocol:** train on `split == "train"` rows of `appeal_queue.csv`. Join `ground_truth.jsonl.gz` **only** to compute metrics. Never use `persona`, `true_outcome`, or `is_false_positive` as features. The customers file ships with `persona` stripped so this is hard to do by accident.

**Report both sides.** Revenue recovered *and* fraud admitted, at every threshold. That is the entire point.
