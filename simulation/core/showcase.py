"""Pick demo cases by what they are, not by payment id.

The six cases in IDEA.md 5 are named by id. Those ids are tied to one seed, and
five of the six sit in the train split, so quoting them means demoing on rows
the model was fitted on. Regenerating the data invalidates the list silently.

Selecting by predicate instead means the demo survives a reseed and always
picks from holdout. Each role below is a description of the shape a case has to
have; the first holdout case matching it, ranked by the role's own tiebreak, is
the one shown.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Role:
    key: str
    title: str
    why: str
    match: object            # (evidence, p_bad, verdict) -> bool
    rank: object             # (evidence, p_bad) -> sortable, higher first


def _clean(v):
    return v.true_outcome == "clean"


ROLES = (
    Role(
        "big_release", "A long clean file released for a large amount",
        "The core case. Every local signal fires, the network file explains all "
        "of them, and the money is real.",
        lambda e, p, v: (_clean(v) and p < 0.05
                         and e.network["network_orders_prior"] >= 40
                         and e.network["network_clean_rate"] >= 0.95),
        lambda e, p: e.amount_inr,
    ),
    Role(
        "clean_looking_fraud", "A spotless record the system still refuses",
        "Separates this from a naive rehabilitator. Perfect clean rate, but "
        "orders spread thinly across many merchants in short tenure is "
        "reconnaissance, not shopping.",
        lambda e, p, v: (not _clean(v) and p > 0.5
                         and e.network["network_clean_rate"] >= 0.99
                         and e.network["network_merchants_prior"] >= 3),
        lambda e, p: p,
    ),
    Role(
        "trivially_bad", "An easy refusal",
        "Not every case is hard, and the system should say so quickly.",
        lambda e, p, v: (not _clean(v) and p > 0.8
                         and e.network["network_disputes_prior"] >= 2),
        lambda e, p: e.amount_inr,
    ),
    Role(
        "abstention", "Too thin to judge",
        "No exculpatory evidence exists, so the honest answer is that there is "
        "no answer. Abstention is a reported outcome, not a hidden fallback.",
        lambda e, p, v: (e.network["network_orders_prior"] < 3
                         and e.amount_inr > 50_000),
        lambda e, p: e.amount_inr,
    ),
    Role(
        "costly_mistake", "The most expensive wrong release",
        "First-party misuse. A real customer with a real history who disputes "
        "anyway, where the exonerating evidence is the same evidence. No "
        "threshold removes this class; the operating point prices it.",
        lambda e, p, v: (not _clean(v) and p < 0.20
                         and e.network["network_orders_prior"] >= 20),
        lambda e, p: e.amount_inr,
    ),
    Role(
        "tier2_refusal", "Refused partly for where they live",
        "High-RTO pincodes are Tier-2 and Tier-3 India. A stack tuned on "
        "regional return rates withdraws from the fastest-growing market.",
        lambda e, p, v: (_clean(v) and p < 0.20
                         and e.local["f_pincode_rto_propensity"] >= 1.2
                         and e.network["network_orders_prior"] >= 20),
        lambda e, p: e.local["f_pincode_rto_propensity"],
    ),
)


def pick(store, vault, model, split: str = "holdout") -> dict:
    """Return {role_key: (evidence, p_bad, verdict)} for the roles that match."""
    cases = store.split(split)
    probs = model.predict(store, cases)
    truth = {v.payment_id: v for v in vault.grade(store.payment_ids(cases))}

    out = {}
    for role in ROLES:
        hits = [(e, p, truth[e.payment_id]) for e, p in zip(cases, probs)
                if role.match(e, p, truth[e.payment_id])]
        if hits:
            out[role.key] = max(hits, key=lambda t: role.rank(t[0], t[1]))
    return out


def table(picked: dict) -> list:
    """Rows for a report or notebook, in ROLES order."""
    rows = []
    for role in ROLES:
        if role.key not in picked:
            rows.append({"role": role.title, "found": False, "why": role.why})
            continue
        e, p, v = picked[role.key]
        rows.append({
            "role": role.title, "found": True, "why": role.why,
            "payment_id": e.payment_id, "merchant": e.merchant,
            "amount_inr": e.amount_inr, "p_bad": float(p),
            "true_outcome": v.true_outcome, "split": e.split,
            "network": dict(e.network),
        })
    return rows
