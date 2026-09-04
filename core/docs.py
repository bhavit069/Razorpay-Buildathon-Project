"""Regenerate the numeric blocks of IDEA.md and DATA_CARD.md in place.

Those two documents were written before there was a harness, so their figures
were typed by hand against a 100k dataset and an earlier seed. They have been
wrong for a while, and hand-patching them is how they got wrong in the first
place.

Every block this module writes sits between markers:

    <!-- GENERATED: name -->
    ...anything in here is overwritten...
    <!-- END GENERATED -->

so the prose around them stays hand-written and the numbers stop being. Run
`python run.py docs` after `python run.py metrics`; `--check` exits non-zero if
a document has drifted. The pre-demo dry run uses that.

Showcase cases come from core.showcase, which selects by predicate against the
holdout, so the worked examples cannot drift back to train-split rows.
"""
from __future__ import annotations

import json
import os
import re
import sys

from .feature_store import FeatureStore
from .model import Adjudicator
from .policy import PolicyConfig, decide
from .showcase import ROLES
from .showcase import pick as pick_showcase
from .truth import TruthVault

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARK = re.compile(
    r"(?P<open><!-- GENERATED: (?P<name>[a-z0-9-]+) -->\n).*?(?P<close><!-- END GENERATED -->)",
    re.S,
)


def cr(x: float) -> str:
    a, sign = abs(x), "-" if x < 0 else ""
    if a >= 1e7:
        return f"{sign}Rs {a / 1e7:,.2f} cr"
    if a >= 1e5:
        return f"{sign}Rs {a / 1e5:,.2f} L"
    return f"{sign}Rs {a:,.0f}"


def pct(x: float) -> str:
    return f"{x:.1%}"


# ---------------------------------------------------------------------------
# Local signal descriptions, so a worked example reads as a sentence
# ---------------------------------------------------------------------------
LOCAL_PROSE = [
    ("f_device_is_new", lambda v: "new device" if v else None),
    ("f_address_mismatch", lambda v: "shipping address differs from billing" if v else None),
    ("f_thin_file_flag", lambda v: "first order at this merchant" if v else None),
    ("f_is_night", lambda v: "ordered at night" if v else None),
    ("f_is_cod", lambda v: "cash on delivery" if v else None),
    ("f_disposable_email", lambda v: "disposable email domain" if v else None),
    ("f_international", lambda v: "international" if v else None),
    ("f_merchant_prior_rto", lambda v: "a prior return at this merchant" if v else None),
    ("f_amount_z", lambda v: f"basket {v:.2f} sd above this merchant's norm" if v >= 1.5 else None),
    ("f_pincode_rto_propensity", lambda v: f"pincode return index {v:.2f}" if v >= 1.15 else None),
    ("f_device_account_fanout", lambda v: f"{int(v)} accounts on the device" if v >= 3 else None),
    ("f_orders_last_24h", lambda v: f"{int(v)} orders in the last 24h" if v >= 3 else None),
]


def local_prose(ev) -> str:
    bits = [f(ev.local[k]) for k, f in LOCAL_PROSE if k in ev.local]
    bits = [b for b in bits if b]
    return ", ".join(bits) if bits else "no individually notable local signal"


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one}" if n == 1 else f"{n} {many or one + 's'}"


def network_prose(ev) -> str:
    n = ev.network
    orders = int(n["network_orders_prior"])
    if orders == 0:
        return (f"no prior orders anywhere on the network, "
                f"{plural(int(n['network_tenure_days']), 'day')} of tenure")
    return (f"{plural(orders, 'prior order')} across "
            f"{plural(int(n['network_merchants_prior']), 'merchant')}, "
            f"{plural(int(n['network_tenure_days']), 'day')} of tenure, "
            f"clean rate {n['network_clean_rate']:.3f}, "
            f"{plural(int(n['network_disputes_prior']), 'prior dispute')}, "
            f"{plural(int(n['network_rto_prior']), 'prior return')}")


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------
def block_idea_portfolio(ctx) -> str:
    st = ctx["stats"]["stats"]
    cfg = ctx["stats"]["config"]
    h = ctx["headline"]
    L = [
        f"Portfolio: {st['payments']:,} orders, {st['merchants']} merchants, "
        f"{cfg['days']} days. The risk stack blocked **{st['blocked']:,}** of them "
        f"({st['block_rate']:.2%}). Of those, **{st['blocked_that_were_good']:,} were good "
        f"customers**, {cr(st['revenue_wrongly_blocked_inr'])} of revenue refused, against "
        f"{cr(st['fraud_correctly_blocked_inr'])} of genuine fraud correctly stopped. "
        f"A ratio of {st['fp_to_tp_value_ratio']:.1f} to 1.",
        "",
        f"The temporal holdout, the split every number in `METRICS.md` is measured on, "
        f"is the last {cfg['holdout_days']} days: {h['n_holdout']:,} blocked orders the model "
        f"never trained on.",
    ]
    return "\n".join(L)


def block_idea_cases(ctx) -> str:
    picked, store = ctx["picked"], ctx["store"]
    cfg = PolicyConfig(cap=ctx["headline"]["cap"])
    L = [
        "Selected by predicate, not by payment id, and only from the holdout. An earlier "
        "revision named six ids by hand; five of them sat in the train split, so the "
        "worked examples were rows the model had been fitted on. Each case below is the "
        "strongest holdout match for a role described by shape, so the list survives a "
        "reseed. Regenerate with `python run.py docs`.",
        "",
    ]
    for i, role in enumerate(ROLES, 1):
        if role.key not in picked:
            L += [f"### Case {i}: {role.title}", "",
                  "No holdout case matches this role in the current dataset.", ""]
            continue
        ev, p, v = picked[role.key]
        d = decide(float(p), ev, cfg)
        L += [
            f"### Case {i}: {role.title}",
            "",
            f"`{ev.payment_id}` at {ev.merchant}, **{cr(ev.amount_inr)}**, "
            f"{ev.meta['method']}, blocked for `{ev.meta['block_reason']}`.",
            "",
            "| | |",
            "|---|---|",
            f"| What the merchant could see | {local_prose(ev)} |",
            f"| Merchant risk score | {ev.local['risk_score']:.3f} against a threshold "
            f"of {ev.meta['threshold']} |",
            f"| What only the network can see | {network_prose(ev)} |",
            f"| Probability the block was correct | **{float(p):.3f}** |",
            f"| Action | **{d.action.value}** |",
            f"| True outcome | `{v.true_outcome}` (persona `{v.persona}`) |",
            "",
            role.why,
            "",
        ]
    return "\n".join(L).rstrip()


def block_idea_frontier(ctx) -> str:
    front, h = ctx["frontier"], ctx["headline"]
    L = [
        "Measured on the holdout. Fraud admitted is printed beside recovery on every row, "
        "and a table without that column would not be honest.",
        "",
        "| cap | Released | Precision | Recall | Recovered | Fraud admitted | Net contribution |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in front:
        star = " **(shipped)**" if abs(r["cap"] - h["cap"]) < 1e-9 else ""
        L.append(f"| {r['cap']}{star} | {r['released']} | {pct(r['precision'])} | "
                 f"{pct(r['recall'])} | {cr(r['recovered'])} | {cr(r['admitted'])} | "
                 f"{cr(r['contribution'])} |")
    L += [
        "",
        f"The shipped operating point is cap {h['cap']}, and it is not tuned. Releasing is "
        f"expected-value positive only below `m/(1+m)`, which at a {ctx['margin']:.0%} margin "
        f"is {h['cap']}. Choosing a lower cap buys a better-looking precision column at the "
        f"cost of recall. That is the behaviour this project exists to criticise.",
    ]
    return "\n".join(L)


def block_idea_results(ctx) -> str:
    h, sc = ctx["headline"], ctx["seeds"]
    L = [
        f"On {h['n_holdout']:,} blocked orders the model never trained on, at cap {h['cap']}:",
        "",
        "| Metric | Value | Across 5 generated worlds |",
        "|---|---|---|",
        f"| **Recall of recoverable** | **{pct(h['recall'])}** | "
        f"{pct(sc['recall_min'])} to {pct(sc['recall_max'])} |",
        f"| Overturn precision | {pct(h['precision'])} | "
        f"{pct(sc['precision_min'])} to {pct(sc['precision_max'])} |",
        f"| Revenue recovered | {cr(h['recovered_inr'])} | - |",
        f"| **Fraud admitted** | **{cr(h['fraud_admitted_inr'])}** | "
        f"{cr(sc['fraud_admitted_min'])} to {cr(sc['fraud_admitted_max'])} |",
        f"| Net contribution | {cr(h['net_contribution_inr'])} | "
        f"{cr(sc['contribution_min'])} to {cr(sc['contribution_max'])} |",
        f"| Abstention rate | {pct(h['abstention_rate'])} | - |",
        "",
        "Read the right-hand column before quoting the middle one. Recall is the steady "
        "quantity. Precision moves "
        f"{100 * (sc['precision_max'] - sc['precision_min']):.1f} points between worlds, and "
        f"money moves about {sc['contribution_max'] / sc['contribution_min']:.1f}x. Each "
        "metric depends on something different: recall on how well the model ranks, "
        "precision on where the threshold lands in a particular world, money on the value "
        "distribution of that world's blocked pile.",
        "",
        f"Deployment assumption: inline at checkout. As a queue review the breakeven is a "
        f"{h['queue']['breakeven_rate']:.1%} customer return rate. `METRICS.md` 11 has the "
        f"arithmetic term by term.",
        "",
        "Baselines that matter, both of them:",
        "",
        "| | Recall of recoverable | Net contribution |",
        "|---|---|---|",
    ]
    hr = h["human_rationed"]
    hb = h["human_baseline"]
    L.append(f"| Human reviewer, all {h['n_holdout']:,} cases | "
             f"{pct(hb['recall']) if 'recall' in hb else '-'} | "
             f"{cr(hb['net_contribution_inr'])} |")
    for k in sorted(hr, key=float):
        L.append(f"| Human reviewer, top {float(k):.0%} by value | {pct(hr[k]['recall'])} | "
                 f"{cr(hr[k]['net_contribution_inr'])} |")
    L += [
        f"| **This system, all {h['n_holdout']:,} cases** | **{pct(h['recall'])}** | "
        f"**{cr(h['net_contribution_inr'])}** |",
        "",
        "A reviewer beats this system case for case and it is left in the table because it "
        "is true. Nobody can afford to run one across the whole pile, so in practice most "
        "cases are never reviewed at all.",
    ]
    return "\n".join(L)


def block_datacard_calibration(ctx) -> str:
    st = ctx["stats"]["stats"]
    return "\n".join([
        "Measured on the current dataset, not typed in.",
        "",
        "| Property | This dataset | Public anchor | Source (dated) |",
        "|---|---|---|---|",
        f"| Orders blocked for risk | **{st['block_rate']:.2%}** | ~2.7% of US domestic "
        "orders declined for fraud concerns (Q3 2023) | ClearSale, retrieved 2026-08-24 |",
        f"| Share of blocked pile that was good | **{st['fp_share_of_blocked_pile']:.1%}** | "
        "30-70% of merchant-declined orders estimated good | Signifyd, via 2026 playbooks |",
        f"| Value wrongly blocked / value correctly blocked | "
        f"**{st['fp_to_tp_value_ratio']:.2f}x** | False declines around 13x fraud prevented "
        "| Javelin (2021), widely re-cited |",
        "| Merchants tracking their false-decline rate | n/a | ~64% | Corgi Labs, 2026-07 |",
        "",
        f"Our false-positive share ({st['fp_share_of_blocked_pile']:.1%}) sits above the "
        f"published 30-70% band, and our value ratio ({st['fp_to_tp_value_ratio']:.2f}x) is "
        "deliberately more conservative than the 13x headline. Both are stated rather than "
        "hidden. The public anchors are vendor-aggregated, several trace back to a single "
        "2021 Javelin study, and India-specific data is thin, so treat them as "
        "order-of-magnitude context.",
    ])


def block_datacard_files(ctx) -> str:
    st = ctx["stats"]["stats"]
    cfg = ctx["stats"]["config"]
    rows = [
        ("payments.jsonl.gz", st["payments"], "Razorpay-shaped payment entities"),
        ("orders.jsonl.gz", st["payments"], "Order entities"),
        ("customers.jsonl.gz", cfg["n_customers"], "Customer entities, **`persona` stripped**"),
        ("risk_decisions.jsonl.gz", st["payments"],
         "Scorecard decision plus point-in-time and network features"),
        ("appeal_queue.csv", st["blocked"], "Blocked orders only, what RECLAIMIFY adjudicates"),
        ("disputes.jsonl", st["disputes"], "Realised chargebacks (allowed orders only)"),
        ("refunds.jsonl", st["refunds_rto"], "Realised RTO refunds (allowed orders only)"),
        ("ground_truth.jsonl.gz", st["payments"],
         "**THE ANSWER KEY.** Reachable only through `core/truth.py`."),
    ]
    L = ["| File | Rows | Contents |", "|---|---|---|"]
    L += [f"| `{n}` | {r:,} | {d} |" for n, r, d in rows]
    L += ["",
          f"Split chronologically: {st['train_rows']:,} train rows and "
          f"{st['holdout_rows']:,} holdout rows, of which {st['holdout_blocked']:,} were "
          f"blocked and form the appeal queue every metric is measured on."]
    return "\n".join(L)


def block_datacard_outcomes(ctx) -> str:
    st = ctx["stats"]["stats"]
    mix, tot = st["true_outcome_mix"], sum(st["true_outcome_mix"].values())
    L = ["What each order *would have done* if allowed through.", "",
         "| True outcome | Orders | Share |", "|---|---|---|"]
    for k, v in sorted(mix.items(), key=lambda kv: -kv[1]):
        L.append(f"| `{k}` | {v:,} | {v / tot:.2%} |")
    fp = st["false_positives_by_persona"]
    fpt = sum(fp.values())
    L += ["", "False positives are not injected, they emerge. Which personas end up in the "
          "blocked pile despite being good:", "",
          "| Persona | Wrongly blocked | Share of the mistakes |", "|---|---|---|"]
    for k, v in sorted(fp.items(), key=lambda kv: -kv[1]):
        L.append(f"| `{k}` | {v:,} | {v / fpt:.1%} |")
    L += ["", "`friendly_fraudster` and `abuser` appear here because on a given order they "
          "behaved. That is why the class is hard: the evidence that exonerates an "
          "honest atypical buyer is the same evidence."]
    return "\n".join(L)


BLOCKS = {
    "idea-portfolio": block_idea_portfolio,
    "idea-cases": block_idea_cases,
    "idea-frontier": block_idea_frontier,
    "idea-results": block_idea_results,
    "datacard-calibration": block_datacard_calibration,
    "datacard-files": block_datacard_files,
    "datacard-outcomes": block_datacard_outcomes,
}


# ---------------------------------------------------------------------------
def context(data_dir="data300k", artifacts="artifacts") -> dict:
    store, vault = FeatureStore.load(data_dir), TruthVault(data_dir)
    model = Adjudicator().fit(store, vault)
    with open(os.path.join(artifacts, "headline.json"), encoding="utf-8") as fh:
        headline = json.load(fh)
    with open(os.path.join(artifacts, "seed_check.json"), encoding="utf-8") as fh:
        seeds = json.load(fh)
    with open(os.path.join(data_dir, "stats.json"), encoding="utf-8") as fh:
        stats = json.load(fh)

    from .backtest import run
    from .metrics import StepUpModel, frontier
    cfg = PolicyConfig(cap=headline["cap"])
    front = frontier(run(store, model, cfg, "holdout"), vault, cfg, StepUpModel())

    return {"store": store, "vault": vault, "model": model, "headline": headline,
            "seeds": seeds, "stats": stats, "frontier": front, "margin": cfg.margin,
            "picked": pick_showcase(store, vault, model)}


def render(path: str, ctx: dict) -> tuple[str, list]:
    """Return the rewritten text and the names of the blocks filled."""
    src = open(path, encoding="utf-8").read()
    filled = []

    def sub(m):
        name = m.group("name")
        if name not in BLOCKS:
            raise KeyError(f"{path}: unknown generated block {name!r}")
        filled.append(name)
        return m.group("open") + BLOCKS[name](ctx) + "\n" + m.group("close")

    return MARK.sub(sub, src), filled


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    check = "--check" in argv
    if check:
        argv.remove("--check")
    data_dir = argv[0] if argv else "data300k"

    ctx = context(data_dir)
    # Both live under docs/. Never reach outside the tree for a file we are
    # about to rewrite: in the packaged copy that resolved to the repo root and
    # a judge running this would have edited files outside their own clone.
    targets = [os.path.join(ROOT, "docs", "IDEA.md"),
               os.path.join(ROOT, "docs", "DATA_CARD.md")]
    drift = 0
    for path in targets:
        path = os.path.normpath(path)
        if not os.path.exists(path):
            print(f"skip {path}, not found")
            continue
        new, filled = render(path, ctx)
        old = open(path, encoding="utf-8").read()
        rel = os.path.relpath(path, os.path.dirname(ROOT))
        if not filled:
            print(f"{rel}: no generated blocks")
            continue
        if new == old:
            print(f"{rel}: up to date ({len(filled)} blocks)")
            continue
        if check:
            print(f"{rel}: DRIFTED, {len(filled)} blocks would change")
            drift += 1
        else:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(new)
            print(f"{rel}: rewrote {len(filled)} blocks: {', '.join(filled)}")
    if check and drift:
        print(f"\n{drift} document(s) drifted. Run `python run.py docs`.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
