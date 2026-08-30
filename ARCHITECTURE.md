# RECLAIMIFY: Architecture & Implementation Plan

Two phases, one invariant:

> **Phase 1 proves the decision system offline. Phase 2 wraps it in an agent.
> The decision system does not change between phases.**

This is the architectural idea judges should be able to repeat back: the agent is a *delivery mechanism* for a policy that was backtested first. Most submissions will build an agent and then look for numbers. RECLAIMIFY builds the numbers and then gives them hands and a voice. Every rupee-moving decision the live agent makes in the demo is a decision the backtest already graded.

---

## 0. System overview

```
┌─────────────────────────── DATA PLANE (done) ───────────────────────────┐
│  generate.py ──► payments / orders / risk_decisions / appeal_queue      │
│                  ground_truth (answer key, quarantined)                 │
└─────────────────────────────────────────────────────────────────────────┘
                                     │
┌─────────────────────── DECISION CORE (Phase 1) ─────────────────────────┐
│                                                                         │
│  feature_store.py   point-in-time evidence assembly (local + network)   │
│  model.py           P(block was correct), GBM + isotonic calibration   │
│  policy.py          EV decision: OVERTURN / UPHOLD / STEP_UP / ESCALATE │
│  backtest.py        chronological replay over the blocked pile          │
│  metrics.py         precision, recall, ₹ ledger, CIs, curves → METRICS.md│
│                                                                         │
│  Pure functions. No LLM anywhere. Fully deterministic given a seed.     │
└─────────────────────────────────────────────────────────────────────────┘
                                     │  (imported unchanged)
┌───────────────────────── AGENT PLANE (Phase 2) ─────────────────────────┐
│                                                                         │
│  orchestrator.py    the case loop: docket → evidence → verdict          │
│  tools.py           deterministic tools the agent calls                 │
│  verdict.py         LLM: written rationale per case                     │
│  stepup.py          LLM: verification dialogue (new-evidence generator) │
│  ledger.py          append-only, hash-chained audit log                 │
│  api.py + ui/       FastAPI + demo dashboard                            │
│                                                                         │
│  The LLM explains and negotiates. It never decides whether money moves. │
└─────────────────────────────────────────────────────────────────────────┘
```

Repo layout:

```
RECLAIMIFY/
├── data/                      # generated, gitignored; regenerate from seed
├── datagen/
│   ├── generate.py            # done
│   └── validate_signal.py     # done
├── core/
│   ├── feature_store.py
│   ├── model.py
│   ├── policy.py
│   ├── backtest.py
│   └── metrics.py
├── agent/
│   ├── orchestrator.py
│   ├── tools.py
│   ├── verdict.py
│   ├── stepup.py
│   └── ledger.py
├── service/
│   ├── api.py
│   └── ui/
├── tests/
│   ├── test_leakage.py        # non-negotiable
│   ├── test_policy.py
│   └── test_replay_determinism.py
├── DATA_CARD.md · IDEA.md · METRICS.md (generated) · README.md
```

---

## PHASE 1: Simulation & backtesting (Days 1 to 3)

The deliverable of this phase is a single generated document, `METRICS.md`, containing every number the pitch will ever quote, with confidence intervals, plus the trained artifacts the agent will later import. If Phase 2 were cancelled, Phase 1 alone would still be a submittable project.

### 1.1 Feature store (`core/feature_store.py`)

One job: given a case, return its evidence vector, and make leakage structurally difficult.

- Input: a row of `appeal_queue.csv` (features were frozen point-in-time by the generator, the store *validates* rather than recomputes).
- Output: a typed `Evidence` object with three named blocks: `local` (12 merchant-visible features), `network` (7 cross-merchant features), `meta` (amount, merchant, timestamps, split).
- Hard rule enforced in code: the store cannot see `ground_truth.jsonl.gz`. It has no import path to it. Truth is joined only inside `metrics.py`.
- Ablation support: `evidence.as_matrix(blocks=["local"])` vs `["local","network"]`, the moat measurement must be one flag, not a code fork.

### 1.2 Adjudication model (`core/model.py`)

- **Target:** `y = 1` if the block was correct (true outcome ≠ clean), `0` if it was a false positive.
- **Model:** gradient-boosted trees (LightGBM if available, sklearn GBM otherwise, both are in the validated baseline). No deep learning: 2 to 3k training rows, tabular, and interpretability is a feature of the pitch.
- **Calibration:** isotonic regression on a time-ordered validation slice carved from the *end* of train. The policy layer consumes probabilities as prices; an uncalibrated 0.2 is a lie that costs money. Ship the reliability diagram in METRICS.md.
- **Split discipline:** temporal only. Train on `split=="train"`, tune on its last 20%, report on `split=="holdout"` exactly once, at the end. If you find yourself re-running holdout to pick parameters, stop, that is how a 0.98 gets manufactured.
- **Artifacts:** `model.pkl`, `calibrator.pkl`, feature manifest with hashes.

### 1.3 Policy layer (`core/policy.py`)

This is where the project stops being a classifier and becomes an economic decision system. Not a global threshold, a **per-case expected-value rule**:

```
EV(release) = (1 − p_bad) · m·A  −  p_bad · (A + f)  −  c_review
```

where `A` = order amount, `m` = merchant contribution margin (config, default 0.25), `f` = dispute/ops overhead on a bad release (config), `c_review` = marginal cost of the case (≈0 for the agent; the point is it *used to be* ₹150+ of human time).

Decision function:

```
if evidence_insufficient(case):            # e.g. network_orders_prior < 3
    → STEP_UP if A ≥ stepup_floor else ESCALATE
elif EV(release) > 0 and p_bad < cap_m:    # cap_m = merchant risk appetite
    → OVERTURN
elif p_bad > uphold_floor:                 # confidently bad
    → UPHOLD
else:
    → STEP_UP if A ≥ stepup_floor else UPHOLD
```

Two properties worth pitching: the same `p_bad` yields different actions at different amounts (a ₹500 ambiguous case is upheld, a ₹5L one is escalated, the system spends effort where money is), and `cap_m` reproduces the operating-point curve: sweeping it *is* the trade-frontier chart. The insufficiency gate is what produced Case 4's abstention, and it must fire on evidence quantity, not model confidence, a model can be confidently wrong on a 1-order file.

### 1.4 Backtest engine (`core/backtest.py`)

Chronological replay, not a batch `.predict()`, because the demo narrative ("day 214: RECLAIMIFY released ₹3.1L, admitted ₹0") and the cumulative ledger chart both fall out of it, and because it catches ordering bugs a batch run hides.

- Iterate the blocked pile in `created_at` order; for each case run `policy.decide(model.predict(features(case)))`; append the decision to a run ledger.
- Join truth **after** the run completes; grade every decision; accumulate daily and cumulative ₹ recovered / ₹ admitted / net.
- **Step-up outcomes in backtest** are the one place a new assumption enters, and it must be explicit: the dataset cannot tell you whether a customer would pass verification. Model it as parameters, `p_pass_given_good` ∈ {0.85, 0.90, 0.95}, `p_pass_given_bad` ∈ {0.03, 0.08, 0.15}, and report results across the grid, never a single point. Declaring this assumption is worth more credibility than hiding it would save embarrassment.
- Determinism test: two runs, same seed, byte-identical ledgers.

### 1.5 Metrics harness (`core/metrics.py` → `METRICS.md`)

Generates, per configuration:

1. Headline table: overturn precision, recall-of-recoverable, ₹ recovered, **₹ fraud admitted**, net ₹, abstention rate, escalation rate, with **bootstrap 95% CIs** (mandatory: the holdout queue is ~620 rows and the intervals are wide; quoting them yourself beats a judge computing them for you).
2. Three baselines: do-nothing, release-everything, **local-features-only model** (the moat isolation).
3. The operating-point frontier (net ₹ vs cap sweep) with per-merchant recommended operating points marked.
4. Ablations: local / +network / network-only; reliability diagram; feature importances with the §DATA_CARD caveat about `network_clean_rate`.
5. Sensitivity appendix: results at `--intercept -3.5 / -4.4 / -4.9 / -5.4` and across the step-up grid.
6. The failure exhibit: the top-5 costliest wrong releases (Case 5 lives here), auto-extracted with their evidence.

### 1.6 Tests that gate Phase 2

- `test_leakage.py`: (a) no feature column correlates with truth via any forbidden join; (b) shuffle test, train on permuted labels, holdout AUC must collapse to ~0.5; (c) time-machine test, assert no feature of case *t* changes when future events are deleted.
- `test_policy.py`: EV monotonicity (higher `p_bad` never increases release propensity at fixed A), amount-sensitivity of abstention.
- `test_replay_determinism.py`.

**Phase 1 definition of done:** `METRICS.md` regenerates from scratch with one command (`make metrics`); holdout precision/recall with CIs; moat ablation ≥ +0.05 AUC; all three test files green. Only then does the agent get built, the agent will be a consumer of these artifacts, and building it against an unproven core is how hackathon weeks die.

---

## PHASE 2: The agent (Days 4 to 6)

The agent adds three things the backtest cannot: **language** (verdicts a merchant can read), **interaction** (the step-up exchange that creates new evidence), and **operations** (queue, audit, escalation). It adds zero new decision logic.

### 2.1 Orchestrator (`agent/orchestrator.py`)

```
for case in docket.stream():                       # chronological, resumable
    ev   = tools.gather_evidence(case)             # deterministic
    p    = tools.adjudicate(ev)                    # core/model.py, unchanged
    act  = tools.policy_decide(p, ev)              # core/policy.py, unchanged
    if act == STEP_UP:
        transcript, new_ev = stepup.run(case, ev)  # LLM dialogue → evidence deltas
        p   = tools.adjudicate(ev + new_ev)        # re-adjudicate: same model
        act = tools.policy_decide(p, ev + new_ev)  # step-up narrows, never overrides
    verdict = verdict.write(case, ev, p, act)      # LLM: language only
    ledger.append(case, ev, p, act, verdict)       # hash-chained
    effects.apply(act)                             # release / uphold / escalate
```

Design rules, each of which is a sentence in the demo:

- **Tool-shaped determinism.** The LLM can only reach the model and policy through tools with fixed signatures. There is no code path where generated text becomes a release decision.
- **Step-up narrows, never overrides.** A verification exchange can move a case from ESCALATE to OVERTURN only by *adding evidence* that the same deterministic policy then re-prices. The LLM's opinion of the conversation is not an input; extracted structured facts are.
- **Escalation is a first-class outcome** with a written brief, not a failure branch.

### 2.2 Tools (`agent/tools.py`)

`get_case`, `get_network_file`, `get_merchant_file`, `get_device_neighbors`, `adjudicate`, `policy_decide`, `queue_stepup`, `record_verdict`, `escalate`. Each is a thin, logged wrapper over Phase 1 functions. The tool log *is* the explainability story: every verdict cites which tools were called and what they returned.

### 2.3 Verdict writer (`agent/verdict.py`)

Claude, with a strict contract: input is the structured evidence + decision + the policy's fired rules; output is a 3 to 6 sentence verdict that (a) states the decision, (b) cites only facts present in the evidence object, (c) names the counter-evidence it weighed. A citation-check pass validates every number in the verdict against the evidence JSON, a verdict that invents a fact fails closed and regenerates. Cheap to build, and it is the difference between "LLM wrote a paragraph" and "auditable rationale."

### 2.4 Step-up loop (`agent/stepup.py`)

Live demo version: a short structured exchange (confirm delivery details, offer prepaid-instead-of-COD, confirm an account-known fact) between the RECLAIMIFY agent and a **simulated customer.** a second LLM instance seeded with the customer's persona from ground truth, so honest customers answer like distracted humans and fraudsters evade like fraudsters. Extracted facts feed re-adjudication. Backtest version stays parametric (§1.4); the live loop is one worked path, not a general system. The prepaid-swap outcome deserves emphasis in the demo: a refused-COD order converted to prepaid carries **zero** residual RTO risk, recovery with no admitted downside.

### 2.5 Ledger (`agent/ledger.py`)

Append-only JSONL; each record carries `sha256(prev_hash + record)`. Twenty lines of code, tamper-evident, and a natural one-liner for your background: *the case file is a chain; you can't rewrite history after the money moves.*

### 2.6 Service & demo UI (`service/`)

FastAPI backend (`/docket`, `/case/{id}`, `/decide`, `/portfolio`, `/curve`), thin React front. Four screens, in demo order:

1. **Docket.** the blocked pile streaming in, decisions resolving live.
2. **Case room.** one case: evidence panel (local vs network side-by-side, the visual version of the moat), verdict, ledger entry. This is where Cases 1, 2 and 4 are shown.
3. **The frontier.** the operating curve with a draggable cap; net ₹ and fraud-admitted update live. The interactive version of the honest-metrics argument.
4. **Portfolio findings.** wrongful-decline rate by block reason and by pincode tier (the Tier-2/3 systemic-refusal finding from Case 3).

Cut-line: if Day 6 runs hot, screens 3 to 4 become static charts and the demo survives; screen 2 is the one that cannot be cut.

---

## Day map

| Day | Build | Gate to pass |
|---|---|---|
| **1** | Repo scaffold; regenerate at `--n 300000` (tighter CIs); feature store; leakage tests | tests green; ablation flag works |
| **2** | Model + calibration; baselines; ablations; bootstrap CIs | holdout run executed once; moat ≥ +0.05 AUC |
| **3** | Policy layer; chronological backtest; `METRICS.md` autogen; sensitivity grid | `make metrics` from clean checkout |
| **4** | Orchestrator + tools + verdict writer + citation check; ledger | 50 cases end-to-end, verdicts audit-clean |
| **5** | Step-up loop (live path); escalation briefs; API | Case-4-type case resolved via step-up on camera |
| **6** | UI; full demo dry-run; failure exhibit polish | 5-min path runs twice without touching code |
| **7** | Video; README; freeze |, |

Standing cut-lines, in order of sacrifice: UI screens 3 to 4 → live step-up (fall back to a recorded run) → per-merchant operating points (fall back to global cap). **Never cut:** leakage tests, CIs, the fraud-admitted column, Case 5.

---

## The risks this architecture is specifically shaped around

**Circularity.** the policy is graded on authored truth. Contained by: calibration to public anchors, the intercept sensitivity grid, emergent-not-injected FPs, and the explicit step-up parameter grid. Named in every document.

**LLM-in-the-money-path.** the credibility killer if a judge finds generated text gating a release. Contained structurally: tools are the only path, step-up narrows-never-overrides, citation-checked verdicts.

**Demo fragility.** live LLM calls fail at the worst moment. Contained: every LLM call in the demo path has a cached replay; the dry-run gate on Day 6 runs off-network once.

**Small holdout.** ~620 cases at 100k. Contained: regenerate at 300k on Day 1 (~1,900 holdout cases), quote CIs regardless.

---

## One paragraph for the video's architecture beat

*"We built this in two layers on purpose. Underneath is a deterministic decision system, a calibrated model and an expected-value policy, that we backtested over a year of synthetic traffic before the agent existed, with the answer key quarantined behind a test suite. On top is the agent: it gathers evidence through tools, talks to customers, and writes the verdicts, but the language model never decides whether money moves. Every number in this demo was graded offline first; the agent is how those decisions get hands."*
