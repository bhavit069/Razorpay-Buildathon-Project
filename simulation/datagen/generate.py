#!/usr/bin/env python3
"""
RECLAIMIFY synthetic dataset generator.

Design principle: FALSE POSITIVES ARE NOT INJECTED. They emerge.

Two independent stages:
  Stage 1 (TRUTH)  -- customers have latent intent; every order has a true
                      counterfactual outcome if it were allowed to proceed.
  Stage 2 (RISK)   -- a merchant risk stack that CANNOT see intent. It scores
                      only observable signals and blocks above a threshold.

Because honest-but-atypical customers emit the same observable signals as
fraudsters (new device, address mismatch, unusual basket, odd hour), the risk
stack blocks some of them. That mismatch IS the false-positive population.
Nobody set a "false positive rate" parameter.

Everything is point-in-time correct: features at decision time use only events
that had already occurred. No leakage.

Usage:
    python generate.py --n 100000 --seed 42 --out ./data
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import random
import string
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone

import numpy as np

# --------------------------------------------------------------------------
# Config -- every knob here is a sensitivity-analysis dial.
# --------------------------------------------------------------------------


@dataclass
class Config:
    n_payments: int = 100_000
    n_customers: int = 12_000
    n_merchants: int = 8
    n_devices_per_customer_mean: float = 1.35
    days: int = 365
    seed: int = 42

    # Latent customer population. Calibrated against public figures where they
    # exist; see DATA_CARD.md for every source and its date.
    persona_mix: dict = field(default_factory=lambda: {
        "legit_stable":       0.700,   # long tenure, predictable
        "legit_new":          0.150,   # thin file -- FP-prone
        "legit_atypical":     0.080,   # travel/gift/bulk/night -- MOST FP-prone
        "friendly_fraudster": 0.040,   # real person, first-party misuse
        "fraudster":          0.024,   # stolen instrument, third-party fraud
        "abuser":             0.006,   # serial RTO / promo abuse
    })

    cod_share: float = 0.34            # India ecom COD share (directional)
    cod_rto_base: float = 0.17         # RTO rate on COD before persona effects

    # Merchant risk stacks: (name, mcc, aov_paise, risk_appetite)
    # risk_appetite -> block threshold. Lower = more paranoid = more FPs.
    merchant_specs: tuple = (
        ("Kirana Direct",     5411,   85_000, 0.62),
        ("Voltcart",          5732, 1_450_000, 0.44),   # electronics, paranoid
        ("Threadline",        5651,  260_000, 0.55),
        ("Aurum Jewels",      5944, 4_200_000, 0.38),   # high-value, very paranoid
        ("PharmaNow",         5912,   62_000, 0.66),
        ("BookNest",          5942,   48_000, 0.70),
        ("HomeHaul",          5712,  880_000, 0.50),
        ("SnackBox",          5499,   34_000, 0.68),
    )

    holdout_days: int = 61             # final ~2 months = held-out test set

    # irreducible noise: outcomes are not a deterministic function of features.
    # Turn this down and the problem gets artificially easy.
    outcome_noise_sigma: float = 0.55

    def outcome_noise_mult(self, np_rng) -> float:
        return float(np.clip(np_rng.lognormal(0.0, self.outcome_noise_sigma), 0.15, 4.0))


CFG = Config()

# --------------------------------------------------------------------------
# Razorpay-shaped identifiers and enums
# --------------------------------------------------------------------------

_ID_ALPHABET = string.ascii_letters + string.digits


def rzp_id(prefix: str, rng: random.Random) -> str:
    """Razorpay object ids: prefix_ + 14 alphanumeric chars."""
    return f"{prefix}_{''.join(rng.choices(_ID_ALPHABET, k=14))}"


BANK_CODES = ["HDFC", "ICIC", "SBIN", "UTIB", "KKBK", "PUNB", "BARB", "YESB", "IDIB", "CNRB"]
UPI_HANDLES = ["okhdfcbank", "okicici", "oksbi", "okaxis", "ybl", "paytm", "apl", "ibl"]
WALLETS = ["payzapp", "freecharge", "mobikwik", "airtelmoney", "phonepe"]
CARD_NETWORKS = ["Visa", "MasterCard", "RuPay", "American Express"]

# error_source enum is per Razorpay docs: customer | business | internal | gateway | issuer_bank
FAILURE_MODES = [
    # (code, description, source, step, reason, weight)
    ("BAD_REQUEST_ERROR", "Payment processing cancelled by user",
     "customer", "payment_authentication", "payment_cancelled", 0.30),
    ("BAD_REQUEST_ERROR", "Your payment did not go through as the transaction was not completed in time",
     "customer", "payment_authentication", "payment_timeout", 0.14),
    ("BAD_REQUEST_ERROR", "Payment failed due to insufficient balance in the account",
     "issuer_bank", "payment_authorization", "insufficient_funds", 0.16),
    ("BAD_REQUEST_ERROR", "Payment was declined by the issuing bank",
     "issuer_bank", "payment_authorization", "payment_failed", 0.18),
    ("BAD_REQUEST_ERROR", "Authentication failed due to incorrect otp",
     "customer", "payment_authentication", "invalid_otp", 0.08),
    ("GATEWAY_ERROR", "Payment failed due to a temporary issue at the bank",
     "gateway", "payment_authorization", "gateway_technical_error", 0.10),
    ("GATEWAY_ERROR", "Payment failed because of downtime at the partner bank",
     "issuer_bank", "payment_authorization", "issuer_down", 0.04),
]

# The merchant risk stack's own vocabulary for why it refused an order.
BLOCK_REASONS = [
    "device_reputation", "address_mismatch", "velocity_burst",
    "amount_anomaly", "geo_risk", "instrument_risk",
    "rto_history", "thin_file_high_value",
]

CITIES = [
    # (city, state, pincode_prefix, regional RTO propensity)
    ("Mumbai", "MH", "400", 0.90), ("Delhi", "DL", "110", 1.15),
    ("Bengaluru", "KA", "560", 0.85), ("Hyderabad", "TG", "500", 0.95),
    ("Chennai", "TN", "600", 0.88), ("Kolkata", "WB", "700", 1.20),
    ("Pune", "MH", "411", 0.87), ("Ahmedabad", "GJ", "380", 1.00),
    ("Jaipur", "RJ", "302", 1.25), ("Lucknow", "UP", "226", 1.35),
    ("Patna", "BR", "800", 1.45), ("Guwahati", "AS", "781", 1.30),
    ("Kochi", "KL", "682", 0.92), ("Indore", "MP", "452", 1.10),
]

FIRST = ["aarav", "vivaan", "aditya", "vihaan", "arjun", "sai", "reyansh", "ayaan",
         "krishna", "ishaan", "ananya", "diya", "aadhya", "myra", "anika", "navya",
         "riya", "meera", "kavya", "saanvi", "rohan", "kabir", "neha", "priya",
         "rahul", "sneha", "vikram", "pooja", "amit", "shreya"]
LAST = ["sharma", "verma", "patel", "reddy", "nair", "iyer", "das", "gupta", "singh",
        "mehta", "shah", "rao", "bose", "kapoor", "joshi", "malhotra", "chatterjee"]

EMAIL_DOMAINS = ["gmail.com", "yahoo.in", "outlook.com", "rediffmail.com", "hotmail.com"]
DISPOSABLE_DOMAINS = ["mailtemp.pro", "tempinbox.co", "trashbox.email", "quickmail.zip"]


# --------------------------------------------------------------------------
# Stage 1: the true world
# --------------------------------------------------------------------------


def build_merchants(cfg: Config, rng: random.Random) -> list[dict]:
    merchants = []
    for name, mcc, aov, appetite in cfg.merchant_specs[: cfg.n_merchants]:
        merchants.append({
            "merchant_id": rzp_id("acc", rng),
            "name": name,
            "mcc": mcc,
            "aov_paise": aov,
            # risk_appetite -> block threshold on a 0-1 score
            "block_threshold": appetite,
            # each merchant weights signals slightly differently
            "weights": {
                "device_new":        rng.uniform(0.7, 1.4),
                "device_fanout":     rng.uniform(0.6, 1.5),
                "addr_mismatch":     rng.uniform(0.8, 1.6),
                "velocity":          rng.uniform(0.6, 1.3),
                "amount_z":          rng.uniform(0.7, 1.8),
                "pincode_rto":       rng.uniform(0.5, 1.4),
                "night":             rng.uniform(0.3, 0.9),
                "thin_file":         rng.uniform(0.8, 1.7),
                "disposable_email":  rng.uniform(1.0, 2.0),
                "international":     rng.uniform(0.9, 2.2),
                "prior_rto":         rng.uniform(1.0, 2.0),
                "cod":               rng.uniform(0.4, 1.1),
            },
        })
    return merchants


PERSONA_PARAMS = {
    # p_fraud_cb: probability an ALLOWED order becomes a third-party fraud dispute
    # p_friendly_cb: probability it becomes first-party misuse
    # rto_mult: multiplier on regional RTO propensity
    # atypicality: how often this persona emits FP-triggering signals
    "legit_stable":       dict(p_fraud_cb=0.0000, p_friendly_cb=0.0025, rto_mult=0.75, atypicality=0.14),
    "legit_new":          dict(p_fraud_cb=0.0000, p_friendly_cb=0.0060, rto_mult=1.05, atypicality=0.34),
    "legit_atypical":     dict(p_fraud_cb=0.0000, p_friendly_cb=0.0075, rto_mult=0.90, atypicality=0.70),
    "friendly_fraudster": dict(p_fraud_cb=0.0000, p_friendly_cb=0.1900, rto_mult=1.25, atypicality=0.38),
    "fraudster":          dict(p_fraud_cb=0.4800, p_friendly_cb=0.0200, rto_mult=1.10, atypicality=0.64),
    "abuser":             dict(p_fraud_cb=0.0100, p_friendly_cb=0.1000, rto_mult=2.20, atypicality=0.52),
}


def build_customers(cfg: Config, rng: random.Random, np_rng: np.random.Generator) -> list[dict]:
    personas = list(cfg.persona_mix.keys())
    probs = np.array([cfg.persona_mix[p] for p in personas], dtype=float)
    probs = probs / probs.sum()
    assigned = np_rng.choice(len(personas), size=cfg.n_customers, p=probs)

    customers = []
    for i in range(cfg.n_customers):
        persona = personas[assigned[i]]
        city, state, pin_prefix, rto_prop = rng.choice(CITIES)
        fn, ln = rng.choice(FIRST), rng.choice(LAST)

        # fraudsters and abusers skew to disposable email + fresh identities
        p_disposable = 0.34 if persona in ("fraudster", "abuser") else 0.09
        domain = rng.choice(DISPOSABLE_DOMAINS) if rng.random() < p_disposable \
            else rng.choice(EMAIL_DOMAINS)

        # network tenure: when this identity first appeared anywhere
        if persona == "legit_stable":
            tenure_days = rng.randint(200, 1400)
        elif persona == "legit_atypical":
            tenure_days = rng.randint(150, 1200)
        elif persona == "legit_new":
            tenure_days = rng.randint(0, 90)
        else:
            tenure_days = rng.randint(0, 45)

        n_dev = max(1, int(np_rng.poisson(cfg.n_devices_per_customer_mean - 1) + 1))
        if persona == "fraudster":
            n_dev = max(n_dev, rng.randint(2, 6))

        customers.append({
            "customer_id": rzp_id("cust", rng),
            "persona": persona,                       # LATENT -- never exposed to the risk stack
            "name": f"{fn.title()} {ln.title()}",
            "email": f"{fn}.{ln}{rng.randint(1, 999)}@{domain}",
            "contact": f"+91{rng.choice('6789')}{''.join(rng.choices('0123456789', k=9))}",
            "city": city, "state": state,
            "pincode": pin_prefix + "".join(rng.choices("0123456789", k=3)),
            "pincode_rto_propensity": rto_prop,
            "disposable_email": domain in DISPOSABLE_DOMAINS,
            "network_tenure_days_at_start": tenure_days,
            "devices": [f"dev_{''.join(rng.choices(_ID_ALPHABET, k=12))}" for _ in range(n_dev)],
            "vpa": f"{fn}.{ln}{rng.randint(1, 99)}@{rng.choice(UPI_HANDLES)}",
            "card_id": rzp_id("card", rng),
            "bank": rng.choice(BANK_CODES),
            "activity": max(0.15, np_rng.gamma(2.0, 0.9)),   # relative order frequency
            # real shoppers concentrate on a few merchants; this is what lets a
            # per-merchant file actually build up, so "thin file" means something
            "home_merchants": None,   # assigned once merchants exist
            # idiosyncratic history noise: honest people pick up disputes for
            # boring reasons; this stops network history being a persona proxy
            "history_noise": float(np.clip(np_rng.lognormal(0.0, 1.15), 0.04, 7.0)),
            # bust-out: farms a clean file, then cashes out. Defeats the
            # "long clean history => safe" shortcut.
            "bustout": (persona == "fraudster" and rng.random() < 0.42),
        })
    return customers


def assign_home_merchants(customers, merchants, rng: random.Random) -> None:
    n = len(merchants)
    for c in customers:
        if c["persona"] in ("fraudster", "abuser"):
            # bust-out behaviour: spray across many merchants, stick to none
            k = rng.randint(max(2, n - 3), n)
        elif c["persona"] == "legit_new":
            k = rng.randint(1, 2)
        else:
            k = rng.randint(1, 3)
        idx = rng.sample(range(n), k)
        # zipfian preference over the chosen merchants
        w = [1.0 / (r + 1) for r in range(k)]
        c["home_merchants"] = (idx, w)


def seed_prior_history(customers, merchants, rng, np_rng,
                       n_orders, n_merchants_seen, n_clean, n_disputes, n_rto):
    """Customers do not spring into existence on day 1 of the window.

    A two-year-old identity should already carry a network file. Without this,
    every early order looks thin-file and RECLAIMIFY has no evidence to work with --
    which would understate the platform's real advantage, not overstate it.
    """
    for c in customers:
        cid = c["customer_id"]
        tenure = c["network_tenure_days_at_start"]
        if tenure <= 0:
            continue
        rate = c["activity"] * 0.020          # orders/day network-wide
        prior = int(np_rng.poisson(max(0.0, rate * tenure)))
        if prior == 0:
            continue
        n_orders[cid] = prior
        idx, _ = c["home_merchants"]
        n_merchants_seen[cid] = {merchants[i]["merchant_id"] for i in idx[: max(1, len(idx))]}

        pp = PERSONA_PARAMS[c["persona"]]
        bad_rate = pp["p_fraud_cb"] + pp["p_friendly_cb"] + 0.05 * pp["rto_mult"]

        # Bust-out fraudsters deliberately farm a clean file before cashing out.
        # Without them, "clean history" would be a free giveaway and RECLAIMIFY would
        # look far better than it deserves to.
        if c.get("bustout"):
            bad_rate *= 0.06

        # Per-customer idiosyncratic history noise. Honest people accumulate
        # disputes for boring reasons (a late delivery, a genuine faulty item),
        # and that noise is what stops network history from being a persona proxy.
        bad_rate = float(np.clip(bad_rate * c["history_noise"], 0.0, 0.85))

        bad = int(np_rng.binomial(prior, bad_rate))
        n_disputes[cid] = int(np_rng.binomial(bad, 0.55))
        n_rto[cid] = bad - n_disputes[cid]
        n_clean[cid] = prior - bad


def build_shared_devices(customers: list[dict], rng: random.Random) -> dict[str, list[str]]:
    """Fraud rings share devices. This creates the device-fanout signal --
    which honest households ALSO trip, at lower magnitude."""
    device_owners: dict[str, list[str]] = defaultdict(list)
    for c in customers:
        for d in c["devices"]:
            device_owners[d].append(c["customer_id"])

    fraud_ids = [c["customer_id"] for c in customers if c["persona"] in ("fraudster", "abuser")]
    rng.shuffle(fraud_ids)
    by_id = {c["customer_id"]: c for c in customers}

    # build rings of 3-9 accounts sharing a device
    i = 0
    while i < len(fraud_ids) - 2:
        size = rng.randint(3, 9)
        ring = fraud_ids[i:i + size]
        i += size
        shared = f"dev_{''.join(rng.choices(_ID_ALPHABET, k=12))}"
        for cid in ring:
            by_id[cid]["devices"].append(shared)
            device_owners[shared].append(cid)

    # honest households: 2-4 accounts share a device (the benign confound)
    legit_ids = [c["customer_id"] for c in customers if c["persona"].startswith("legit")]
    rng.shuffle(legit_ids)
    n_households = len(legit_ids) // 14
    j = 0
    for _ in range(n_households):
        size = rng.randint(2, 4)
        household = legit_ids[j:j + size]
        j += size
        if len(household) < 2:
            break
        shared = f"dev_{''.join(rng.choices(_ID_ALPHABET, k=12))}"
        for cid in household:
            by_id[cid]["devices"].append(shared)
            device_owners[shared].append(cid)

    return device_owners


# --------------------------------------------------------------------------
# Stage 2: the merchant risk stack (blind to persona)
# --------------------------------------------------------------------------


INTERCEPT = -4.90


def risk_score(feats: dict, weights: dict) -> float:
    """A plausible merchant fraud scorecard. Sees ONLY observable signals.

    Deliberately imperfect: it keys on proxies that correlate with fraud but
    also fire on honest-atypical behaviour. That overlap produces the false
    positives RECLAIMIFY exists to review.
    """
    z = INTERCEPT
    z += weights["device_new"]       * 0.85 * feats["device_is_new"]
    z += weights["device_fanout"]    * 0.62 * min(max(0, feats["device_account_fanout"] - 1), 7)
    z += weights["addr_mismatch"]    * 0.78 * feats["address_mismatch"]
    z += weights["velocity"]         * 0.42 * min(feats["orders_last_24h"], 6)
    z += weights["amount_z"]         * 0.46 * max(0.0, feats["amount_z"])
    z += weights["pincode_rto"]      * 0.70 * (feats["pincode_rto_propensity"] - 1.0)
    z += weights["night"]            * 0.22 * feats["is_night"]
    z += weights["thin_file"]        * 0.62 * feats["thin_file_flag"]
    z += weights["disposable_email"] * 1.30 * feats["disposable_email"]
    z += weights["international"]    * 1.05 * feats["international"]
    z += weights["prior_rto"]        * 0.80 * min(feats["merchant_prior_rto"], 3)
    z += weights["cod"]              * 0.20 * feats["is_cod"]
    return 1.0 / (1.0 + math.exp(-z))


def pick_block_reason(feats: dict, rng: random.Random) -> str:
    candidates = []
    if feats["device_is_new"] or feats["device_account_fanout"] >= 2:
        candidates.append("device_reputation")
    if feats["address_mismatch"]:
        candidates.append("address_mismatch")
    if feats["orders_last_24h"] >= 3:
        candidates.append("velocity_burst")
    if feats["amount_z"] > 1.2:
        candidates.append("amount_anomaly")
    if feats["pincode_rto_propensity"] > 1.15:
        candidates.append("geo_risk")
    if feats["disposable_email"] or feats["international"]:
        candidates.append("instrument_risk")
    if feats["merchant_prior_rto"] > 0:
        candidates.append("rto_history")
    if feats["thin_file_flag"] and feats["amount_z"] > 0.6:
        candidates.append("thin_file_high_value")
    return rng.choice(candidates) if candidates else rng.choice(BLOCK_REASONS)


# --------------------------------------------------------------------------
# Main generation loop
# --------------------------------------------------------------------------


def generate(cfg: Config, outdir: str) -> dict:
    rng = random.Random(cfg.seed)
    np_rng = np.random.default_rng(cfg.seed)

    merchants = build_merchants(cfg, rng)
    customers = build_customers(cfg, rng, np_rng)
    device_owners = build_shared_devices(customers, rng)
    by_id = {c["customer_id"]: c for c in customers}

    end = datetime(2026, 8, 1, tzinfo=timezone.utc)
    start = end - timedelta(days=cfg.days)
    holdout_start = end - timedelta(days=cfg.holdout_days)

    # Draw which customer makes each order, weighted by activity
    weights = np.array([c["activity"] for c in customers], dtype=float)
    weights /= weights.sum()
    order_customers = np_rng.choice(len(customers), size=cfg.n_payments, p=weights)

    # Timestamps: uniform over the window, then sorted so history accrues causally
    offsets = np.sort(np_rng.uniform(0, cfg.days * 86400, size=cfg.n_payments))

    # ---- running point-in-time state (never looks forward) ----
    m_orders = defaultdict(int)         # (cust, merchant) -> prior order count
    m_first_seen = {}                   # (cust, merchant) -> first ts
    m_rto = defaultdict(int)            # (cust, merchant) -> prior RTOs
    n_orders = defaultdict(int)         # cust -> prior orders network-wide
    n_merchants_seen = defaultdict(set)
    n_clean = defaultdict(int)
    n_disputes = defaultdict(int)
    n_rto = defaultdict(int)
    n_first_seen = {}
    device_seen = defaultdict(set)      # device -> customers seen on it so far
    recent_orders = defaultdict(list)   # cust -> [ts] for velocity

    assign_home_merchants(customers, merchants, rng)
    seed_prior_history(customers, merchants, rng, np_rng,
                       n_orders, n_merchants_seen, n_clean, n_disputes, n_rto)

    payments, orders_out, decisions, truths = [], [], [], []
    disputes, refunds = [], []

    for i in range(cfg.n_payments):
        c = customers[order_customers[i]]
        cid = c["customer_id"]
        idx, w = c["home_merchants"]
        m = merchants[rng.choices(idx, weights=w)[0]]
        mid = m["merchant_id"]
        ts = start + timedelta(seconds=float(offsets[i]))
        ts_unix = int(ts.timestamp())
        pp = PERSONA_PARAMS[c["persona"]]

        # ---------------- observable signals at decision time ----------------
        atypical = rng.random() < pp["atypicality"]

        device = rng.choice(c["devices"]) if not atypical else \
            (f"dev_{''.join(rng.choices(_ID_ALPHABET, k=12))}" if rng.random() < 0.5
             else rng.choice(c["devices"]))
        device_is_new = int(cid not in device_seen[device])
        fanout = len(device_seen[device] | {cid})

        address_mismatch = int(atypical and rng.random() < 0.55)
        is_night = int(ts.hour < 6 or ts.hour >= 23)
        if atypical and rng.random() < 0.4:
            is_night = 1

        is_cod = int(rng.random() < cfg.cod_share)
        international = int(atypical and rng.random() < 0.10)

        # basket size: lognormal around merchant AOV, inflated when atypical
        mult = float(np_rng.lognormal(mean=0.0, sigma=0.55))
        if atypical and rng.random() < 0.5:
            mult *= rng.uniform(1.8, 5.0)          # gift / bulk / holiday order
        amount = max(1000, int(m["aov_paise"] * mult))
        amount_z = math.log(amount / m["aov_paise"]) / 0.55

        # velocity: prior orders by this customer in trailing 24h
        window = [t for t in recent_orders[cid] if ts_unix - t < 86400]
        recent_orders[cid] = window
        orders_last_24h = len(window)
        if c["persona"] in ("fraudster", "abuser") and rng.random() < 0.35:
            orders_last_24h += rng.randint(1, 4)

        prior_here = m_orders[(cid, mid)]
        thin_file_flag = int(prior_here == 0)

        feats = {
            "device_is_new": device_is_new,
            "device_account_fanout": fanout,
            "address_mismatch": address_mismatch,
            "orders_last_24h": orders_last_24h,
            "amount_z": amount_z,
            "pincode_rto_propensity": c["pincode_rto_propensity"],
            "is_night": is_night,
            "thin_file_flag": thin_file_flag,
            "disposable_email": int(c["disposable_email"]),
            "international": international,
            "merchant_prior_rto": m_rto[(cid, mid)],
            "is_cod": is_cod,
        }

        score = risk_score(feats, m["weights"])
        blocked = score >= m["block_threshold"]

        # ---------------- ground truth: what WOULD have happened ----------------
        rto_p = cfg.cod_rto_base * pp["rto_mult"] * c["pincode_rto_propensity"] if is_cod else 0.0
        rto_p = min(rto_p * (1.35 if address_mismatch else 1.0) * cfg.outcome_noise_mult(np_rng), 0.85)

        roll = rng.random()
        if c["persona"] == "fraudster":
            # stolen instrument: disputed or not, this order is never "good"
            true_outcome = "chargeback_fraud" if roll < pp["p_fraud_cb"] else "fraud_undisputed"
        elif roll < pp["p_fraud_cb"] * c["history_noise"]:
            true_outcome = "chargeback_fraud"
        elif roll < (pp["p_fraud_cb"] + pp["p_friendly_cb"]) * c["history_noise"]:
            true_outcome = "chargeback_friendly"
        elif rng.random() < rto_p:
            true_outcome = "rto_return"
        else:
            true_outcome = "clean"

        # ---------------- network features (RECLAIMIFY's unfair advantage) ----------------
        net_tenure = c["network_tenure_days_at_start"] + (ts - start).days
        if cid in n_first_seen:
            net_tenure = max(net_tenure, int((ts_unix - n_first_seen[cid]) / 86400))
        net_prior = n_orders[cid]
        net_completion = (n_clean[cid] / net_prior) if net_prior else 0.0

        network = {
            "network_orders_prior": net_prior,
            "network_merchants_prior": len(n_merchants_seen[cid]),
            "network_tenure_days": net_tenure,
            "network_clean_rate": round(net_completion, 4),
            "network_disputes_prior": n_disputes[cid],
            "network_rto_prior": n_rto[cid],
            "network_device_fanout": fanout,
            "network_instrument_merchants": len(n_merchants_seen[cid]),
        }

        # ---------------- emit Razorpay-shaped entities ----------------
        order_id = rzp_id("order", rng)
        payment_id = rzp_id("pay", rng)
        method = "cod" if is_cod else rng.choices(
            ["upi", "card", "netbanking", "wallet", "emi"],
            weights=[0.62, 0.22, 0.09, 0.05, 0.02])[0]

        # An order the risk stack refused never reaches the gateway.
        if blocked:
            pay_status = "created"
            captured = False
            err = (None, None, None, None, None)
        else:
            # allowed orders still fail at the gateway sometimes (unrelated to fraud)
            if method != "cod" and rng.random() < 0.115:
                pay_status, captured = "failed", False
                fm = rng.choices(FAILURE_MODES, weights=[f[5] for f in FAILURE_MODES])[0]
                err = fm[0], fm[1], fm[2], fm[3], fm[4]
            else:
                pay_status, captured = "captured", True
                err = (None, None, None, None, None)

        fee = int(amount * 0.0236) if (captured and method != "cod") else 0
        payment = {
            "id": payment_id,
            "entity": "payment",
            "amount": amount,
            "currency": "INR",
            "status": pay_status,
            "order_id": order_id,
            "invoice_id": None,
            "international": bool(international),
            "method": method,
            "amount_refunded": 0,
            "refund_status": None,
            "captured": captured,
            "description": f"Order at {m['name']}",
            "card_id": c["card_id"] if method in ("card", "emi") else None,
            "bank": c["bank"] if method == "netbanking" else None,
            "wallet": rng.choice(WALLETS) if method == "wallet" else None,
            "vpa": c["vpa"] if method == "upi" else None,
            "email": c["email"],
            "contact": c["contact"],
            "customer_id": cid,
            "notes": {"merchant": m["name"], "channel": "web" if rng.random() < 0.42 else "app"},
            "fee": fee,
            "tax": int(fee * 0.18),
            "error_code": err[0],
            "error_description": err[1],
            "error_source": err[2],
            "error_step": err[3],
            "error_reason": err[4],
            "acquirer_data": (
                {"rrn": "".join(rng.choices(string.digits, k=12))} if method in ("upi", "card", "emi")
                else {"bank_transaction_id": "".join(rng.choices(string.digits, k=10))} if method == "netbanking"
                else {"transaction_id": "".join(rng.choices(string.digits, k=12))} if method == "wallet"
                else {}
            ),
            "created_at": ts_unix,
        }
        if method == "upi":
            payment["upi"] = {"payer_account_type": "bank_account", "vpa": c["vpa"]}
        if method == "cod":
            # MODELLED EXTENSION, not a Razorpay payment-method enum value.
            # COD lives at the order level in Magic Checkout. Flagged so nobody
            # mistakes this for a real API field.
            payment["_synthetic_extension"] = "cod_modelled_as_method"
        payments.append(payment)

        orders_out.append({
            "id": order_id,
            "entity": "order",
            "amount": amount,
            "amount_paid": amount if captured else 0,
            "amount_due": 0 if captured else amount,
            "currency": "INR",
            "receipt": f"rcpt_{i:07d}",
            "status": "paid" if captured else "created",
            "attempts": 1 if not blocked else 0,
            "notes": {"merchant_id": mid, "cod": bool(is_cod), "pincode": c["pincode"]},
            "created_at": ts_unix,
        })

        decisions.append({
            "decision_id": rzp_id("dec", rng),
            "payment_id": payment_id,
            "order_id": order_id,
            "merchant_id": mid,
            "merchant_name": m["name"],
            "customer_id": cid,
            "created_at": ts_unix,
            "risk_score": round(score, 5),
            "threshold": m["block_threshold"],
            "action": "block" if blocked else "allow",
            "block_reason": pick_block_reason(feats, rng) if blocked else None,
            "amount": amount,
            "method": method,
            "is_cod": bool(is_cod),
            **{f"f_{k}": v for k, v in feats.items()},
            **network,
            "split": "holdout" if ts >= holdout_start else "train",
        })

        # ---- ANSWER KEY: kept in a separate file, never joined at train time ----
        truths.append({
            "payment_id": payment_id,
            "order_id": order_id,
            "customer_id": cid,
            "persona": c["persona"],
            "true_outcome": true_outcome,
            "was_blocked": blocked,
            # the population RECLAIMIFY exists to find:
            "is_false_positive": bool(blocked and true_outcome == "clean"),
            "is_true_positive": bool(blocked and true_outcome != "clean"),
            "amount": amount,
            "created_at": ts_unix,
            "split": "holdout" if ts >= holdout_start else "train",
        })

        # ---- realised downstream events for ALLOWED orders only ----
        if not blocked and captured:
            if true_outcome in ("chargeback_fraud", "chargeback_friendly"):
                disputes.append({
                    "id": rzp_id("disp", rng),
                    "entity": "dispute",
                    "payment_id": payment_id,
                    "amount": amount,
                    "currency": "INR",
                    "amount_deducted": 0,
                    "reason_code": "fraud" if true_outcome == "chargeback_fraud" else "goods_services_not_received",
                    "phase": "chargeback",
                    "status": "open",
                    "respond_by": ts_unix + 7 * 86400,
                    "created_at": ts_unix + rng.randint(3, 45) * 86400,
                })
            if true_outcome == "rto_return":
                refunds.append({
                    "id": rzp_id("rfnd", rng),
                    "entity": "refund",
                    "amount": amount,
                    "currency": "INR",
                    "payment_id": payment_id,
                    "notes": {"reason": "rto"},
                    "receipt": None,
                    "acquirer_data": {"arn": "".join(rng.choices(string.digits, k=12))},
                    "created_at": ts_unix + rng.randint(2, 20) * 86400,
                    "status": "processed",
                    "speed_processed": "normal",
                })

        # ---------------- advance point-in-time state ----------------
        device_seen[device].add(cid)
        recent_orders[cid].append(ts_unix)
        if cid not in n_first_seen:
            n_first_seen[cid] = ts_unix
        if (cid, mid) not in m_first_seen:
            m_first_seen[(cid, mid)] = ts_unix

        if not blocked:
            m_orders[(cid, mid)] += 1
            n_orders[cid] += 1
            n_merchants_seen[cid].add(mid)
            if true_outcome == "clean":
                n_clean[cid] += 1
            elif true_outcome.startswith("chargeback"):
                n_disputes[cid] += 1
            elif true_outcome == "rto_return":
                n_rto[cid] += 1
                m_rto[(cid, mid)] += 1

    # ---------------- write ----------------
    os.makedirs(outdir, exist_ok=True)

    def dump(name, rows, gz=True):
        path = os.path.join(outdir, name + (".jsonl.gz" if gz else ".jsonl"))
        opener = gzip.open if gz else open
        with opener(path, "wt", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, separators=(",", ":")) + "\n")
        return path

    dump("payments", payments)
    dump("orders", orders_out)
    dump("customers", [{k: v for k, v in c.items() if k not in ("persona", "home_merchants")} for c in customers])
    dump("disputes", disputes, gz=False)
    dump("refunds", refunds, gz=False)
    dump("risk_decisions", decisions)
    dump("ground_truth", truths)            # THE ANSWER KEY -- do not join at train time

    # flat modelling table for the blocked pile only (what RECLAIMIFY adjudicates)
    import csv
    blocked_rows = [d for d in decisions if d["action"] == "block"]
    if blocked_rows:
        with open(os.path.join(outdir, "appeal_queue.csv"), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(blocked_rows[0].keys()))
            w.writeheader()
            w.writerows(blocked_rows)

    # sample files for eyeballing
    with open(os.path.join(outdir, "sample_payments.json"), "w") as fh:
        json.dump(payments[:5], fh, indent=2)

    # ---------------- stats ----------------
    n_blocked = sum(1 for t in truths if t["was_blocked"])
    n_fp = sum(1 for t in truths if t["is_false_positive"])
    n_tp = sum(1 for t in truths if t["is_true_positive"])
    fp_amt = sum(t["amount"] for t in truths if t["is_false_positive"])
    tp_amt = sum(t["amount"] for t in truths if t["is_true_positive"])
    missed = [t for t in truths if not t["was_blocked"] and t["true_outcome"] != "clean"]

    stats = {
        "payments": len(payments),
        "customers": len(customers),
        "merchants": len(merchants),
        "blocked": n_blocked,
        "block_rate": round(n_blocked / len(truths), 4),
        "blocked_that_were_good": n_fp,
        "fp_share_of_blocked_pile": round(n_fp / n_blocked, 4) if n_blocked else 0,
        "blocked_that_were_bad": n_tp,
        "revenue_wrongly_blocked_inr": round(fp_amt / 100, 2),
        "fraud_correctly_blocked_inr": round(tp_amt / 100, 2),
        "fp_to_tp_value_ratio": round(fp_amt / tp_amt, 2) if tp_amt else None,
        "leaked_past_risk_stack": len(missed),
        "disputes": len(disputes),
        "refunds_rto": len(refunds),
        "train_rows": sum(1 for t in truths if t["split"] == "train"),
        "holdout_rows": sum(1 for t in truths if t["split"] == "holdout"),
        "holdout_blocked": sum(1 for t in truths if t["split"] == "holdout" and t["was_blocked"]),
    }
    by_outcome = defaultdict(int)
    for t in truths:
        by_outcome[t["true_outcome"]] += 1
    stats["true_outcome_mix"] = dict(by_outcome)

    fp_personas = defaultdict(int)
    for t in truths:
        if t["is_false_positive"]:
            fp_personas[t["persona"]] += 1
    stats["false_positives_by_persona"] = dict(fp_personas)

    with open(os.path.join(outdir, "stats.json"), "w") as fh:
        json.dump({"config": asdict(cfg), "stats": stats}, fh, indent=2, default=str)

    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100_000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--customers", type=int, default=None,
                    help="Number of customers. Default scales with --n to hold "
                         "orders-per-customer at the calibrated 100k baseline (8.33). "
                         "Pass an explicit value to override.")
    ap.add_argument("--out", default="./data")
    ap.add_argument("--intercept", type=float, default=None,
                    help="Override risk scorecard intercept (calibration dial).")
    ap.add_argument("--fp-sensitivity", type=float, default=1.0,
                    help="Scale all block thresholds. <1 = more paranoid merchants.")
    args = ap.parse_args()

    if args.intercept is not None:
        global INTERCEPT
        INTERCEPT = args.intercept

    # Orders-per-customer is a calibration-critical ratio: the scorecard's
    # velocity, thin-file and history signals all key off it. Holding n_customers
    # fixed while raising --n thickens every file and inflates the block rate,
    # so scale the population with the payment count unless told otherwise.
    baseline = Config()
    n_customers = args.customers if args.customers is not None else max(
        1, round(baseline.n_customers * args.n / baseline.n_payments)
    )
    cfg = Config(n_payments=args.n, n_customers=n_customers, seed=args.seed)
    if args.fp_sensitivity != 1.0:
        cfg.merchant_specs = tuple(
            (n, m, a, round(t * args.fp_sensitivity, 4)) for n, m, a, t in cfg.merchant_specs
        )

    stats = generate(cfg, args.out)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
