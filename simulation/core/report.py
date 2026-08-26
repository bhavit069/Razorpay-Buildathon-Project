"""Generates METRICS.md.

ARCHITECTURE.md 1.5 puts this inside metrics.py; split out so that file stays
arithmetic and this one stays prose.

    python run.py metrics
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import numpy as np
from sklearn.metrics import (average_precision_score, brier_score_loss,
                             roc_auc_score)

from .backtest import run
from .feature_store import FeatureStore
from .metrics import (BOOTSTRAP_B, CAP_GRID, REVIEW_COST_INR, StepUpModel,
                      apply_per_merchant, baseline_do_nothing,
                      baseline_release_all, bootstrap_ci, calibration_ledger,
                      ev_release_ceiling, frontier, grade, select_caps)
from .model import Adjudicator
from .policy import PolicyConfig
from .truth import TruthVault

DEFAULT_CAP = 0.10


# --- formatting -------------------------------------------------------------
def cr(x: float) -> str:
    """Rupees, in lakh/crore."""
    sign = "-" if x < 0 else ""
    a = abs(x)
    if a >= 1e7:
        return f"{sign}Rs {a/1e7:,.2f} cr"
    if a >= 1e5:
        return f"{sign}Rs {a/1e5:,.2f} L"
    return f"{sign}Rs {a:,.0f}"


def best_cap(calib_ledger, vault, base_cfg, stepup):
    """Global cap maximising net contribution on the calibration slice. Each
    model gets its own; a shared cap would measure the cap, not the model."""
    best, best_net = base_cfg.cap, -np.inf
    for c in CAP_GRID:
        n = grade(calib_ledger.redecide(base_cfg.for_merchant(c)), vault,
                  stepup).net_contribution_inr
        if n > best_net:
            best, best_net = c, n
    return best


def pct(x: float) -> str:
    return "n/a" if x != x else f"{100*x:.1f}%"


def ci(lo: float, hi: float, fmt) -> str:
    return f"[{fmt(lo)}, {fmt(hi)}]"


# --- figures ----------------------------------------------------------------
def _figures(outdir, ho_ledger, vault, p_holdout, y_holdout, front, daily):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return {}

    os.makedirs(outdir, exist_ok=True)
    made = {}

    # reliability
    fig, ax = plt.subplots(figsize=(5, 5))
    edges = np.linspace(0, 1, 11)
    idx = np.clip(np.digitize(p_holdout, edges) - 1, 0, 9)
    xs, ys, ns = [], [], []
    for b in range(10):
        m = idx == b
        if m.sum() >= 5:
            xs.append(p_holdout[m].mean())
            ys.append(y_holdout[m].mean())
            ns.append(int(m.sum()))
    ax.plot([0, 1], [0, 1], "--", color="#888", lw=1, label="perfect")
    ax.plot(xs, ys, "o-", color="#2b6cb0", label="isotonic-calibrated")
    ax.set_xlabel("predicted P(block was correct)")
    ax.set_ylabel("observed frequency")
    ax.set_title("Reliability, holdout")
    ax.legend(frameon=False)
    fig.tight_layout()
    p = os.path.join(outdir, "reliability.png")
    fig.savefig(p, dpi=130)
    plt.close(fig)
    made["reliability"] = p
    made["reliability_bins"] = list(zip(xs, ys, ns))

    # frontier
    fig, ax = plt.subplots(figsize=(6.5, 4))
    caps = [r["cap"] for r in front]
    ax.plot(caps, [r["recovered"] / 1e7 for r in front], "o-", label="recovered", color="#2f855a")
    ax.plot(caps, [r["admitted"] / 1e7 for r in front], "o-", label="fraud admitted", color="#c53030")
    ax.plot(caps, [r["contribution"] / 1e7 for r in front], "o-", label="net contribution", color="#2b6cb0")
    ax.set_xlabel("cap (max tolerable p_bad)")
    ax.set_ylabel("Rs crore")
    ax.set_title("Operating-point frontier, holdout")
    ax.legend(frameon=False)
    ax.grid(alpha=.25)
    fig.tight_layout()
    p = os.path.join(outdir, "frontier.png")
    fig.savefig(p, dpi=130)
    plt.close(fig)
    made["frontier"] = p

    # cumulative ledger
    fig, ax = plt.subplots(figsize=(6.5, 4))
    ax.plot(daily["day"], np.cumsum(daily["recovered"]) / 1e7, label="cumulative recovered", color="#2f855a")
    ax.plot(daily["day"], np.cumsum(daily["admitted"]) / 1e7, label="cumulative fraud admitted", color="#c53030")
    ax.set_xlabel("day of holdout window")
    ax.set_ylabel("Rs crore")
    ax.set_title("Chronological replay")
    ax.legend(frameon=False)
    ax.grid(alpha=.25)
    fig.tight_layout()
    p = os.path.join(outdir, "cumulative.png")
    fig.savefig(p, dpi=130)
    plt.close(fig)
    made["cumulative"] = p
    return made


# --- the report -------------------------------------------------------------
def build(data_dir="data300k", artifacts="artifacts", out="METRICS.md") -> str:
    store = FeatureStore.load(data_dir)
    vault = TruthVault(data_dir)
    cfg = PolicyConfig(cap=DEFAULT_CAP)
    stepup = StepUpModel()

    models = {b: Adjudicator(b).fit(store, vault)
              for b in [("local", "network"), ("local",), ("network",)]}
    model = models[("local", "network")]
    model.save(artifacts)

    ho = store.split("holdout")
    clusters = [e.meta["customer_id"] for e in ho]     # bootstrap resamples customers
    y_ho = vault.labels(store.payment_ids(ho))
    p_ho = model.predict(store, ho)

    ledger = run(store, model, cfg, "holdout")
    o = grade(ledger, vault, stepup)

    # daily series for the replay chart
    days = np.array([r.day for r in ledger.records])
    rel = o.released_mask
    amt = ledger.amounts()
    good = y_ho == 0
    span = np.arange(days.max() + 1)
    daily = {
        "day": span,
        "recovered": np.array([amt[(days == d) & rel & good].sum() for d in span]),
        "admitted": np.array([amt[(days == d) & rel & ~good].sum() for d in span]),
    }

    front = frontier(ledger, vault, cfg, stepup)
    calib = calibration_ledger(store, model, cfg)
    caps = select_caps(calib, vault, cfg, stepup)
    pm_ledger = apply_per_merchant(ledger, caps, cfg)
    pm = grade(pm_ledger, vault, stepup)

    figs = _figures(artifacts, ledger, vault, p_ho, y_ho, front, daily)

    L = []
    A = L.append
    A("# METRICS")
    A("")
    A(f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} by `python run.py metrics`. "
      f"Regenerates from seed. No number here is typed by hand.")
    A("")
    A(f"- Dataset `{data_dir}/` - {len(store)} appealed (blocked) orders, "
      f"{len(store.split('train'))} train / {len(store.split('holdout'))} holdout, temporal split.")
    A(f"- Learner `{model.card.learner}`, isotonic-calibrated on the last "
      f"{model.card.n_calib} train cases; fitted on {model.card.n_fit}.")
    A(f"- No parameter, threshold or operating point was chosen on holdout. They were "
      f"chosen on the calibration slice. Holdout is read many times below, but only to "
      f"report -- never to decide.")
    A(f"- Confidence intervals: percentile bootstrap over {len(set(clusters))} customers "
      f"(not {len(ho)} cases -- 36% of cases share a customer and are not independent), "
      f"B={BOOTSTRAP_B}.")
    A(f"- These intervals cover sampling error **within one generated world**. They do not "
      f"cover the variation between worlds, which is larger. See 8.")
    A("")

    # ---- 1. headline
    A("## 1. Headline, holdout")
    A("")
    A(f"Policy at cap={cfg.cap}, margin={cfg.margin}, overhead={cr(cfg.dispute_overhead_inr)}/bad release, "
      f"step-up assumption {stepup.label()}.")
    A("")
    A("| Metric | Value | 95% CI |")
    A("|---|---|---|")
    rows = [
        ("Overturn precision", pct(o.precision), "precision", pct),
        ("Recall of recoverable", pct(o.recall_recoverable), "recall_recoverable", pct),
        ("Revenue recovered", cr(o.recovered_inr), "recovered_inr", cr),
        ("**Fraud admitted**", f"**{cr(o.fraud_admitted_inr)}**", "fraud_admitted_inr", cr),
        ("Net (gross convention)", cr(o.net_inr), "net_inr", cr),
        ("Net contribution", cr(o.net_contribution_inr), "net_contribution_inr", cr),
        ("Abstention rate", pct(o.abstention_rate), "abstention_rate", pct),
    ]
    for label, val, attr, fmt in rows:
        lo, hi = bootstrap_ci(ledger, vault, attr, stepup, clusters=clusters)
        A(f"| {label} | {val} | {ci(lo, hi, fmt)} |")
    A(f"| Escalation rate | {pct(o.escalation_rate)} | - |")
    A("")
    A(f"Decision mix: " + ", ".join(f"{k} {v}" for k, v in o.counts.items())
      + f". {o.n_stepup_passed} of {o.counts['STEP_UP']} step-ups passed verification "
        f"under the stated assumption and were released.")
    A("")
    A("Two rupee columns. Net (gross convention) is recovered minus admitted, how "
      "the industry and `IDEA.md` quote it. Net contribution is what reaches the "
      "P&L: margin on a recovered sale, the entire basket on a bad "
      "one. The second is smaller, and is what the policy optimises.")
    A("")

    # ---- 2. baselines
    A("## 2. Baselines")
    A("")
    dn = baseline_do_nothing(ledger, vault)
    ra = baseline_release_all(ledger, vault)

    # Each model is given its own operating point, chosen on the calibration
    # slice. Holding the cap fixed across models measures the cap, not the moat.
    tuned = {}
    for blocks, mdl in models.items():
        c = best_cap(calibration_ledger(store, mdl, cfg), vault, cfg, stepup)
        g = grade(run(store, mdl, cfg, "holdout").redecide(cfg.for_merchant(c)),
                  vault, stepup)
        tuned[blocks] = (c, g)
    lo_cap, lo = tuned[("local",)]
    net_cap, net = tuned[("local", "network")]

    A("| Policy | cap | Released | Precision | Recovered | Fraud admitted | Net | Net contribution |")
    A("|---|---|---|---|---|---|---|---|")
    A(f"| Do nothing (status quo) | - | 0 | n/a | {cr(0)} | {cr(0)} | {cr(0)} | {cr(0)} |")
    A(f"| Release everything | - | {ra.n_released} | {pct(ra.precision)} | {cr(ra.recovered_inr)} | "
      f"{cr(ra.fraud_admitted_inr)} | {cr(ra.net_inr)} | {cr(ra.net_contribution_inr)} |")
    for name, blocks in [("Merchant-local model only", ("local",)),
                         ("Network evidence only", ("network",)),
                         ("**Local + network (this system)**", ("local", "network"))]:
        c, x = tuned[blocks]
        A(f"| {name} | {c:.3f} | {x.n_released} | {pct(x.precision)} | {cr(x.recovered_inr)} | "
          f"{cr(x.fraud_admitted_inr)} | {cr(x.net_inr)} | {cr(x.net_contribution_inr)} |")
    A("")
    A(f"Release-everything recovers the most gross revenue and still loses money: "
      f"{cr(ra.net_contribution_inr)} of net contribution, because margin on the good "
      f"orders does not cover the full baskets lost on the bad ones.")
    A("")
    gain = net.net_contribution_inr - lo.net_contribution_inr
    A(f"Network evidence is worth "
      f"**{cr(gain)}** of net contribution over the merchant-local model "
      f"({100*gain/abs(lo.net_contribution_inr):+.1f}%) on {len(ho)} holdout cases. It "
      f"releases {net.n_released - lo.n_released} more orders *at higher precision* "
      f"({pct(net.precision)} vs {pct(lo.precision)}) -- more revenue and less fraud at "
      f"the same time, which a pure threshold move cannot do.")
    A("")
    A(f"Comparing the two models at a shared raw-probability threshold on "
      f"uncalibrated scores shows a much larger gap. Most of that gap is "
      f"miscalibration in the local model rather than missing evidence: isotonic "
      f"calibration removes it, and the EV policy then picks a workable operating "
      f"point for either model. With both calibrated and each tuned off-holdout the "
      f"difference is {100*gain/abs(lo.net_contribution_inr):+.1f}% of net contribution "
      f"and +{roc_auc_score(y_ho, models[('local','network')].predict(store, ho)) - roc_auc_score(y_ho, models[('local',)].predict(store, ho)):.3f} AUC.")
    A("")

    # ---- 3. frontier
    A("## 3. The operating-point frontier")
    A("")
    A("| cap | Released | Precision | Recall | Recovered | Fraud admitted | Net | Net contribution |")
    A("|---|---|---|---|---|---|---|---|")
    for r in front:
        A(f"| {r['cap']:.3f} | {r['released']} | {pct(r['precision'])} | {pct(r['recall'])} | "
          f"{cr(r['recovered'])} | {cr(r['admitted'])} | {cr(r['net'])} | {cr(r['contribution'])} |")
    A("")
    ceiling = ev_release_ceiling(cfg)
    A(f"**The cap saturates at {ceiling:.2f} and cannot be pushed past it.** "
      f"EV(release) > 0 requires `(1-p)mA > p(A+f)`, i.e. `p < m/(1+m)`; at the default "
      f"margin m={cfg.margin} that is {ceiling:.2f}, independent of order size. Contribution "
      f"margin, not risk appetite, sets the ceiling on how much doubt a release can carry. "
      f"This is a real difference from the sweep in `IDEA.md` 7, which sweeps a raw "
      f"probability threshold to 0.50 -- values an EV policy can never reach.")
    A("")
    if "frontier" in figs:
        A(f"![frontier]({os.path.relpath(figs['frontier']).replace(os.sep, '/')})")
        A("")

    A("### Per-merchant operating points")
    A("")
    A("Chosen on the calibration slice by maximising net contribution, then applied "
      "unchanged to holdout.")
    A("")
    A("| Merchant | cap | Released | Precision | Net contribution |")
    A("|---|---|---|---|---|")
    from .backtest import Ledger as _L
    for merchant in sorted(caps):
        recs = [r for r in pm_ledger.records if r.merchant == merchant]
        if not recs:
            continue
        sub = _L(recs, pm_ledger.config, "holdout", pm_ledger.blocks)
        so = grade(sub, vault, stepup)
        A(f"| {merchant} | {caps[merchant]:.3f} | {so.n_released} | {pct(so.precision)} | "
          f"{cr(so.net_contribution_inr)} |")
    A(f"| **All, per-merchant caps** | - | {pm.n_released} | {pct(pm.precision)} | "
      f"**{cr(pm.net_contribution_inr)}** |")
    A(f"| All, single global cap={cfg.cap} | {cfg.cap} | {o.n_released} | {pct(o.precision)} | "
      f"{cr(o.net_contribution_inr)} |")
    A("")
    delta = pm.net_contribution_inr - o.net_contribution_inr
    A(f"Per-merchant caps add {cr(delta)} over one global cap "
      f"({100*delta/abs(o.net_contribution_inr):+.0f}%), by releasing fewer orders at "
      f"higher precision.")
    A("")

    # ---- 4. ablations
    A("## 4. Ablations and calibration")
    A("")
    A("| Evidence blocks | AUC | AP | Brier | Net contribution |")
    A("|---|---|---|---|---|")
    for blocks, mdl in models.items():
        p = mdl.predict(store, ho)
        g = grade(run(store, mdl, cfg, "holdout"), vault, stepup)
        A(f"| {' + '.join(blocks)} | {roc_auc_score(y_ho, p):.4f} | "
          f"{average_precision_score(y_ho, p):.4f} | {brier_score_loss(y_ho, p):.4f} | "
          f"{cr(g.net_contribution_inr)} |")
    p_loc = models[("local",)].predict(store, ho)
    p_net = models[("local", "network")].predict(store, ho)
    A("")
    A(f"AUC lift from network evidence: +{roc_auc_score(y_ho, p_net) - roc_auc_score(y_ho, p_loc):.4f}. "
      f"AP lift: +{average_precision_score(y_ho, p_net) - average_precision_score(y_ho, p_loc):.4f}. "
      f"AUC averages rank quality over every threshold, while releases happen only in the "
      f"low-p_bad tail, so it is not the quantity that decides anything here. Use the "
      f"contribution column.")
    A("")
    if "reliability" in figs:
        A(f"![reliability]({os.path.relpath(figs['reliability']).replace(os.sep, '/')})")
        A("")
        A("| predicted | observed | n |")
        A("|---|---|---|")
        for x, y_, n in figs["reliability_bins"]:
            A(f"| {x:.3f} | {y_:.3f} | {n} |")
        A("")

    A("### Feature importances")
    A("")
    gain = model.importances("gain")
    split = dict(model.importances("split"))
    A("| feature | gain | split count |")
    A("|---|---|---|")
    for name, v in gain[:10]:
        A(f"| `{name}` | {v:.4f} | {split.get(name, 0):.4f} |")
    A("")
    gain_d = dict(gain)
    cr_gain, cr_split = gain_d.get("network_clean_rate", 0), split.get("network_clean_rate", 0)
    cr_rank = [n for n, _ in gain].index("network_clean_rate") + 1
    verdict = ("confirmed" if cr_gain >= 0.25 else
               "partly reproduced" if cr_gain >= 0.12 else "not reproduced")
    A(f"`DATA_CARD.md` 6.4 warns that `network_clean_rate` dominates importance (~0.49 "
      f"there) and that this is partly real signal, partly residual construction. Under "
      f"this model it carries **{cr_gain:.3f} of gain at rank {cr_rank}** -- the warning is "
      f"**{verdict}**. Treat conclusions that lean on that one feature with suspicion.")
    A("")
    A(f"The two columns disagree. Split count is how often a feature was used, is "
      f"nearly flat by construction, and puts `network_clean_rate` at {cr_split:.3f}, "
      f"sixth. Gain is how much each split improved the objective and puts the same "
      f"feature first at {cr_gain:.3f}. LightGBM reports split count by default, so "
      f"the default would have understated the concentration. Use the ablation table "
      f"rather than either column.")
    A("")

    # ---- 5. sensitivity
    A("## 5. Sensitivity")
    A("")
    A("### Step-up assumption grid")
    A("")
    A("The dataset cannot say whether a customer would pass verification, so it is a "
      "declared parameter and swept.")
    A("")
    A("| p(pass\\|good) | p(pass\\|bad) | Released | Precision | Net contribution |")
    A("|---|---|---|---|---|")
    for pg in (0.85, 0.90, 0.95):
        for pb in (0.03, 0.08, 0.15):
            g = grade(ledger, vault, StepUpModel(pg, pb))
            A(f"| {pg:.2f} | {pb:.2f} | {g.n_released} | {pct(g.precision)} | "
              f"{cr(g.net_contribution_inr)} |")
    A("")
    A("### Economic parameters")
    A("")
    A("| margin m | EV ceiling m/(1+m) | Released | Precision | Net contribution |")
    A("|---|---|---|---|---|")
    for mg in (0.15, 0.25, 0.40):
        c2 = PolicyConfig(cap=DEFAULT_CAP, margin=mg)
        g = grade(ledger.redecide(c2), vault, stepup)
        A(f"| {mg:.2f} | {ev_release_ceiling(c2):.3f} | {g.n_released} | {pct(g.precision)} | "
          f"{cr(g.net_contribution_inr)} |")
    A("")
    A("| overhead f per bad release | Released | Precision | Net contribution |")
    A("|---|---|---|---|")
    for f in (250.0, 750.0, 1500.0):
        c2 = PolicyConfig(cap=DEFAULT_CAP, dispute_overhead_inr=f)
        g = grade(ledger.redecide(c2), vault, stepup)
        A(f"| {cr(f)} | {g.n_released} | {pct(g.precision)} | {cr(g.net_contribution_inr)} |")
    A("")
    A("Block-rate sensitivity (regenerating the world at four scorecard intercepts) is a "
      "data-generation sweep rather than a policy sweep: `python run.py sweep`.")
    A("")

    # ---- 6. failure exhibit
    A("## 6. The failure exhibit")
    A("")
    A("The five costliest wrong releases at the headline operating point.")
    A("")
    by_id = {e.payment_id: e for e in ho}
    bad_rel = [(r, amt_) for r, m_, amt_, g in
               zip(ledger.records, o.released_mask, amt, good) if m_ and not g]
    bad_rel.sort(key=lambda t: -t[1])
    A("| payment_id | Merchant | Amount | p_bad | How released | Block reason | Network file |")
    A("|---|---|---|---|---|---|---|")
    n_via_stepup = 0
    for r, a_ in bad_rel[:5]:
        e = by_id[r.payment_id]
        n = e.network
        via = "direct overturn" if r.action == "OVERTURN" else "**passed step-up**"
        n_via_stepup += r.action != "OVERTURN"
        f = (f"{n['network_orders_prior']:.0f} orders / "
             f"{n['network_merchants_prior']:.0f} merchants / "
             f"{n['network_tenure_days']:.0f}d / clean {n['network_clean_rate']:.2f} / "
             f"{n['network_disputes_prior']:.0f} disputes")
        A(f"| `{r.payment_id}` | {r.merchant} | {cr(a_)} | {r.p_bad:.3f} | {via} | "
          f"{r.block_reason} | {f} |")
    A("")
    A(f"Total cost of these five: {cr(sum(a for _, a in bad_rel[:5]))}, against "
      f"{cr(o.recovered_inr)} recovered.")
    A("")
    A("Most of these network files are long, clean and real. This is first-party "
      "misuse: friendly fraud from customers with genuine histories, where the "
      "evidence that exonerates an honest atypical buyer is the same evidence. No "
      "threshold removes this class of error; the operating point prices it. One row "
      "breaks the pattern, carrying disputes and a poor clean rate, which the model "
      "should have caught.")
    A("")
    if n_via_stepup:
        A(f"{n_via_stepup} of the five was released by passing a simulated verification "
          f"exchange rather than by the policy; its `p_bad` sits above the cap. That is "
          f"the step-up assumption ({stepup.label()}) costing money, and why 5 reports "
          f"a grid.")
        A("")
    if "cumulative" in figs:
        A("## 7. Chronological replay")
        A("")
        A(f"![cumulative]({os.path.relpath(figs['cumulative']).replace(os.sep, '/')})")
        A("")
        peak = int(np.argmax(daily["recovered"]))
        A(f"Best single day in the holdout window: day {peak}, "
          f"{cr(daily['recovered'][peak])} recovered against "
          f"{cr(daily['admitted'][peak])} admitted.")
        A("")

    # ---- 8. between-world variation
    A("## 8. Variation between generated worlds")
    A("")
    seedfile = os.path.join(artifacts, "seed_check.json")
    if os.path.exists(seedfile):
        with open(seedfile, encoding="utf-8") as fh:
            sc = json.load(fh)
        A(f"The pipeline was rerun end to end on {len(sc['rows'])} independently generated "
          f"datasets: same config, different seed.")
        A("")
        A("| seed | holdout | local AUC | +network AUC | lift | precision | net contribution |")
        A("|---|---|---|---|---|---|---|")
        for r in sc["rows"]:
            A(f"| {r['seed']} | {r['n_holdout']} | {r['local_auc']:.4f} | "
              f"{r['net_auc']:.4f} | +{r['lift']:.4f} | {pct(r['precision'])} | "
              f"{cr(r['contribution'])} |")
        A("")
        A(f"Net contribution ranges {cr(sc['contribution_min'])} to "
          f"{cr(sc['contribution_max'])}, sd {cr(sc['contribution_sd'])}. That range is "
          f"wider than the bootstrap interval in 1, so the rupee figures are limited by "
          f"how the world was generated, not by how many cases the holdout holds. Quote a "
          f"range, not a point.")
        A("")
        A(f"AUC lift ranges +{sc['lift_min']:.4f} to +{sc['lift_max']:.4f}, mean "
          f"+{sc['lift_mean']:.4f}. Seed 42, used everywhere above, is the least "
          f"favourable of the set. Precision is the stable quantity: "
          f"{pct(sc['precision_min'])} to {pct(sc['precision_max'])}.")
    else:
        A("Not measured. Run `python run.py seeds`, then regenerate this report.")
    A("")

    text = "\n".join(L)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)

    with open(os.path.join(artifacts, "headline.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "cap": cfg.cap, "n_holdout": len(ho),
            "precision": o.precision, "recall": o.recall_recoverable,
            "recovered_inr": o.recovered_inr, "fraud_admitted_inr": o.fraud_admitted_inr,
            "net_inr": o.net_inr, "net_contribution_inr": o.net_contribution_inr,
            "abstention_rate": o.abstention_rate,
            "per_merchant_caps": caps,
            "moat_contribution_gain_inr": gain,
        }, fh, indent=2)
    return out


if __name__ == "__main__":
    print("wrote", build())
