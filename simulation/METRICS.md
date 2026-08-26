# METRICS

Generated 2026-08-26 20:37 UTC by `python run.py metrics`. Regenerates from seed. No number here is typed by hand.

- Dataset `data300k/` - 8265 appealed (blocked) orders, 6490 train / 1775 holdout, temporal split.
- Learner `lightgbm`, isotonic-calibrated on the last 1298 train cases; fitted on 5192.
- No parameter, threshold or operating point was chosen on holdout. They were chosen on the calibration slice. Holdout is read many times below, but only to report -- never to decide.
- Confidence intervals: percentile bootstrap over 1400 customers (not 1775 cases -- 36% of cases share a customer and are not independent), B=2000.
- These intervals cover sampling error **within one generated world**. They do not cover the variation between worlds, which is larger. See 8.

## 1. Headline, holdout

Policy at cap=0.1, margin=0.25, overhead=Rs 750/bad release, step-up assumption good=0.90/bad=0.08.

| Metric | Value | 95% CI |
|---|---|---|
| Overturn precision | 96.7% | [95.7%, 97.6%] |
| Recall of recoverable | 87.8% | [86.0%, 89.6%] |
| Revenue recovered | Rs 7.20 cr | [Rs 6.48 cr, Rs 7.94 cr] |
| **Fraud admitted** | **Rs 48.33 L** | [Rs 28.50 L, Rs 72.29 L] |
| Net (gross convention) | Rs 6.71 cr | [Rs 5.92 cr, Rs 7.46 cr] |
| Net contribution | Rs 1.31 cr | [Rs 99.92 L, Rs 1.58 cr] |
| Abstention rate | 16.3% | [14.6%, 18.0%] |
| Escalation rate | 0.5% | - |

Decision mix: OVERTURN 1110, UPHOLD 376, STEP_UP 280, ESCALATE 9. 193 of 280 step-ups passed verification under the stated assumption and were released.

Two rupee columns. Net (gross convention) is recovered minus admitted, how the industry and `IDEA.md` quote it. Net contribution is what reaches the P&L: margin on a recovered sale, the entire basket on a bad one. The second is smaller, and is what the policy optimises.

## 2. Baselines

| Policy | cap | Released | Precision | Recovered | Fraud admitted | Net | Net contribution |
|---|---|---|---|---|---|---|---|
| Do nothing (status quo) | - | 0 | n/a | Rs 0 | Rs 0 | Rs 0 | Rs 0 |
| Release everything | - | 1775 | 80.8% | Rs 7.43 cr | Rs 1.92 cr | Rs 5.51 cr | -Rs 8.79 L |
| Merchant-local model only | 0.005 | 993 | 98.3% | Rs 6.68 cr | Rs 20.18 L | Rs 6.47 cr | Rs 1.47 cr |
| Network evidence only | 0.050 | 802 | 98.1% | Rs 6.54 cr | Rs 20.67 L | Rs 6.34 cr | Rs 1.43 cr |
| **Local + network (this system)** | 0.020 | 1181 | 98.6% | Rs 7.04 cr | Rs 22.57 L | Rs 6.82 cr | Rs 1.53 cr |

Release-everything recovers the most gross revenue and still loses money: -Rs 8.79 L of net contribution, because margin on the good orders does not cover the full baskets lost on the bad ones.

Network evidence is worth **Rs 6.76 L** of net contribution over the merchant-local model (+4.6%) on 1775 holdout cases. It releases 188 more orders *at higher precision* (98.6% vs 98.3%) -- more revenue and less fraud at the same time, which a pure threshold move cannot do.

Comparing the two models at a shared raw-probability threshold on uncalibrated scores shows a much larger gap. Most of that gap is miscalibration in the local model rather than missing evidence: isotonic calibration removes it, and the EV policy then picks a workable operating point for either model. With both calibrated and each tuned off-holdout the difference is +4.6% of net contribution and +0.045 AUC.

## 3. The operating-point frontier

| cap | Released | Precision | Recall | Recovered | Fraud admitted | Net | Net contribution |
|---|---|---|---|---|---|---|---|
| 0.005 | 1124 | 98.5% | 77.1% | Rs 6.95 cr | Rs 22.57 L | Rs 6.72 cr | Rs 1.51 cr |
| 0.010 | 1124 | 98.5% | 77.1% | Rs 6.95 cr | Rs 22.57 L | Rs 6.72 cr | Rs 1.51 cr |
| 0.020 | 1181 | 98.6% | 81.1% | Rs 7.04 cr | Rs 22.57 L | Rs 6.82 cr | Rs 1.53 cr |
| 0.030 | 1196 | 98.6% | 82.2% | Rs 7.08 cr | Rs 22.57 L | Rs 6.86 cr | Rs 1.54 cr |
| 0.050 | 1213 | 98.4% | 83.2% | Rs 7.11 cr | Rs 23.23 L | Rs 6.88 cr | Rs 1.54 cr |
| 0.075 | 1245 | 98.2% | 85.2% | Rs 7.14 cr | Rs 25.30 L | Rs 6.89 cr | Rs 1.53 cr |
| 0.100 | 1303 | 96.7% | 87.8% | Rs 7.20 cr | Rs 48.33 L | Rs 6.71 cr | Rs 1.31 cr |
| 0.125 | 1303 | 96.7% | 87.8% | Rs 7.20 cr | Rs 48.33 L | Rs 6.71 cr | Rs 1.31 cr |
| 0.150 | 1314 | 96.3% | 88.2% | Rs 7.21 cr | Rs 51.21 L | Rs 6.70 cr | Rs 1.29 cr |
| 0.175 | 1314 | 96.3% | 88.2% | Rs 7.21 cr | Rs 51.21 L | Rs 6.70 cr | Rs 1.29 cr |
| 0.200 | 1314 | 96.3% | 88.2% | Rs 7.21 cr | Rs 51.21 L | Rs 6.70 cr | Rs 1.29 cr |

**The cap saturates at 0.20 and cannot be pushed past it.** EV(release) > 0 requires `(1-p)mA > p(A+f)`, i.e. `p < m/(1+m)`; at the default margin m=0.25 that is 0.20, independent of order size. Contribution margin, not risk appetite, sets the ceiling on how much doubt a release can carry. This is a real difference from the sweep in `IDEA.md` 7, which sweeps a raw probability threshold to 0.50 -- values an EV policy can never reach.

![frontier](artifacts/frontier.png)

### Per-merchant operating points

Chosen on the calibration slice by maximising net contribution, then applied unchanged to holdout.

| Merchant | cap | Released | Precision | Net contribution |
|---|---|---|---|---|
| Aurum Jewels | 0.020 | 351 | 97.4% | Rs 1.25 cr |
| BookNest | 0.100 | 15 | 100.0% | Rs 7,672 |
| HomeHaul | 0.075 | 253 | 98.4% | Rs 19.04 L |
| Kirana Direct | 0.100 | 137 | 98.5% | Rs 1.11 L |
| PharmaNow | 0.100 | 133 | 99.2% | Rs 91,047 |
| SnackBox | 0.100 | 106 | 98.1% | Rs 44,634 |
| Threadline | 0.075 | 166 | 98.2% | Rs 3.44 L |
| Voltcart | 0.005 | 100 | 98.0% | Rs 12.50 L |
| **All, per-merchant caps** | - | 1261 | 98.2% | **Rs 1.54 cr** |
| All, single global cap=0.1 | 0.1 | 1303 | 96.7% | Rs 1.31 cr |

Per-merchant caps add Rs 22.27 L over one global cap (+17%), by releasing fewer orders at higher precision.

## 4. Ablations and calibration

| Evidence blocks | AUC | AP | Brier | Net contribution |
|---|---|---|---|---|
| local + network | 0.9134 | 0.7633 | 0.0781 | Rs 1.31 cr |
| local | 0.8682 | 0.6189 | 0.1026 | Rs 1.35 cr |
| network | 0.7768 | 0.6498 | 0.0926 | Rs 1.02 cr |

AUC lift from network evidence: +0.0452. AP lift: +0.1444. AUC averages rank quality over every threshold, while releases happen only in the low-p_bad tail, so it is not the quantity that decides anything here. Use the contribution column.

![reliability](artifacts/reliability.png)

| predicted | observed | n |
|---|---|---|
| 0.020 | 0.036 | 1125 |
| 0.138 | 0.200 | 30 |
| 0.211 | 0.240 | 333 |
| 0.364 | 0.383 | 94 |
| 0.572 | 0.727 | 44 |
| 0.750 | 1.000 | 15 |
| 0.982 | 0.977 | 132 |

### Feature importances

| feature | gain | split count |
|---|---|---|
| `network_clean_rate` | 0.3455 | 0.0853 |
| `network_merchants_prior` | 0.1857 | 0.0617 |
| `f_is_cod` | 0.1207 | 0.0334 |
| `network_tenure_days` | 0.0532 | 0.1381 |
| `network_orders_prior` | 0.0521 | 0.0931 |
| `amount` | 0.0362 | 0.1145 |
| `risk_score` | 0.0360 | 0.1187 |
| `f_orders_last_24h` | 0.0338 | 0.0193 |
| `f_amount_z` | 0.0337 | 0.1162 |
| `network_rto_prior` | 0.0229 | 0.0404 |

`DATA_CARD.md` 6.4 warns that `network_clean_rate` dominates importance (~0.49 there) and that this is partly real signal, partly residual construction. Under this model it carries **0.346 of gain at rank 1** -- the warning is **confirmed**. Treat conclusions that lean on that one feature with suspicion.

The two columns disagree. Split count is how often a feature was used, is nearly flat by construction, and puts `network_clean_rate` at 0.085, sixth. Gain is how much each split improved the objective and puts the same feature first at 0.346. LightGBM reports split count by default, so the default would have understated the concentration. Use the ablation table rather than either column.

## 5. Sensitivity

### Step-up assumption grid

The dataset cannot say whether a customer would pass verification, so it is a declared parameter and swept.

| p(pass\|good) | p(pass\|bad) | Released | Precision | Net contribution |
|---|---|---|---|---|
| 0.85 | 0.03 | 1288 | 96.9% | Rs 1.32 cr |
| 0.85 | 0.08 | 1291 | 96.7% | Rs 1.27 cr |
| 0.85 | 0.15 | 1296 | 96.3% | Rs 1.26 cr |
| 0.90 | 0.03 | 1300 | 96.9% | Rs 1.35 cr |
| 0.90 | 0.08 | 1303 | 96.7% | Rs 1.31 cr |
| 0.90 | 0.15 | 1308 | 96.3% | Rs 1.30 cr |
| 0.95 | 0.03 | 1303 | 96.9% | Rs 1.35 cr |
| 0.95 | 0.08 | 1306 | 96.7% | Rs 1.31 cr |
| 0.95 | 0.15 | 1311 | 96.3% | Rs 1.30 cr |

### Economic parameters

| margin m | EV ceiling m/(1+m) | Released | Precision | Net contribution |
|---|---|---|---|---|
| 0.15 | 0.130 | 1290 | 96.7% | Rs 59.31 L |
| 0.25 | 0.200 | 1303 | 96.7% | Rs 1.31 cr |
| 0.40 | 0.286 | 1305 | 96.6% | Rs 2.39 cr |

| overhead f per bad release | Released | Precision | Net contribution |
|---|---|---|---|
| Rs 250 | 1306 | 96.6% | Rs 1.32 cr |
| Rs 750 | 1303 | 96.7% | Rs 1.31 cr |
| Rs 1,500 | 1298 | 96.7% | Rs 1.31 cr |

Block-rate sensitivity (regenerating the world at four scorecard intercepts) is a data-generation sweep rather than a policy sweep: `python run.py sweep`.

## 6. The failure exhibit

The five costliest wrong releases at the headline operating point.

| payment_id | Merchant | Amount | p_bad | How released | Block reason | Network file |
|---|---|---|---|---|---|---|
| `pay_uVSI51jHI2v7T4` | Aurum Jewels | Rs 5.14 L | 0.098 | direct overturn | amount_anomaly | 5 orders / 1 merchants / 359d / clean 1.00 / 0 disputes |
| `pay_PUyUECOg4UTiTE` | Aurum Jewels | Rs 4.19 L | 0.098 | direct overturn | amount_anomaly | 19 orders / 3 merchants / 1443d / clean 0.95 / 0 disputes |
| `pay_N2qffKv4UTooIi` | Aurum Jewels | Rs 4.03 L | 0.098 | direct overturn | address_mismatch | 5 orders / 2 merchants / 395d / clean 1.00 / 0 disputes |
| `pay_xKq663PUe3u0Co` | Aurum Jewels | Rs 3.84 L | 0.204 | **passed step-up** | instrument_risk | 73 orders / 3 merchants / 1215d / clean 1.00 / 0 disputes |
| `pay_5KVTMvf8syNwYA` | Aurum Jewels | Rs 3.80 L | 0.098 | direct overturn | amount_anomaly | 9 orders / 2 merchants / 342d / clean 0.44 / 3 disputes |

Total cost of these five: Rs 20.99 L, against Rs 7.20 cr recovered.

Most of these network files are long, clean and real. This is first-party misuse: friendly fraud from customers with genuine histories, where the evidence that exonerates an honest atypical buyer is the same evidence. No threshold removes this class of error; the operating point prices it. One row breaks the pattern, carrying disputes and a poor clean rate, which the model should have caught.

1 of the five was released by passing a simulated verification exchange rather than by the policy; its `p_bad` sits above the cap. That is the step-up assumption (good=0.90/bad=0.08) costing money, and why 5 reports a grid.

## 7. Chronological replay

![cumulative](artifacts/cumulative.png)

Best single day in the holdout window: day 12, Rs 27.63 L recovered against Rs 0 admitted.

## 8. Variation between generated worlds

The pipeline was rerun end to end on 5 independently generated datasets: same config, different seed.

| seed | holdout | local AUC | +network AUC | lift | precision | net contribution |
|---|---|---|---|---|---|---|
| 42 | 1775 | 0.8682 | 0.9134 | +0.0452 | 98.6% | Rs 1.53 cr |
| 1 | 1745 | 0.8501 | 0.9209 | +0.0709 | 97.8% | Rs 1.87 cr |
| 2 | 1486 | 0.8414 | 0.9066 | +0.0651 | 98.4% | Rs 1.15 cr |
| 3 | 1826 | 0.8602 | 0.9321 | +0.0719 | 99.1% | Rs 1.80 cr |
| 4 | 1800 | 0.8678 | 0.9109 | +0.0431 | 98.5% | Rs 1.19 cr |

Net contribution ranges Rs 1.15 cr to Rs 1.87 cr, sd Rs 29.78 L. That range is wider than the bootstrap interval in 1, so the rupee figures are limited by how the world was generated, not by how many cases the holdout holds. Quote a range, not a point.

AUC lift ranges +0.0431 to +0.0719, mean +0.0592. Seed 42, used everywhere above, is the least favourable of the set. Precision is the stable quantity: 97.8% to 99.1%.
