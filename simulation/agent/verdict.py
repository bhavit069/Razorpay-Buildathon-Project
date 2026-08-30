"""Writes the per-case verdict and checks the numbers in it.

The model receives the evidence, the decision, and the rules that fired. The
decision is already made and passed in, so the model cannot change it.

check_citations then requires every number in the prose to be derivable from
the evidence. A verdict that invents a figure is regenerated.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.feature_store import Evidence
from core.policy import Action, Decision

from .llm import Completion, LLMClient

MAX_ATTEMPTS = 3

SYSTEM = """You write the case note for a payments risk review system.

A merchant's fraud stack blocked an order. A calibrated model and an
expected-value policy have already re-examined it and reached a decision. Your
only job is to explain that decision in plain English for the merchant.

Rules, all of them hard:
- 3 to 6 sentences. No headings, no bullet points, no preamble.
- State the decision in the first sentence.
- Cite only figures present in the evidence you are given. Never estimate,
  round to a nicer number, or introduce a figure that is not there.
- Name the counter-evidence. If the decision was to release, say what still
  looks bad about the order. If it was to uphold, say what looked good.
- Do not speculate about the customer's intent or circumstances. You know their
  transaction record and nothing else.
- Do not recommend a different decision. It is not yours to make.

Write for someone who will read a hundred of these. Be flat and specific."""


@dataclass
class Verdict:
    payment_id: str
    text: str
    source: str            # anthropic | gemini | cache | template
    attempts: int
    citations_ok: bool
    rejected: tuple = ()   # numbers that failed the check, if any
    model: str | None = None   # which model wrote it, None for a template

    @property
    def audited(self) -> bool:
        return self.citations_ok

    @property
    def from_model(self) -> bool:
        return self.source in ("anthropic", "gemini", "cache")

    @property
    def provenance(self) -> str:
        """What to print next to the text. A reader must never have to work out
        whether they are looking at model output or a filled-in template, and a
        screenshot must not imply the first when it is the second."""
        if not self.from_model:
            return "deterministic template, not model output"
        return self.model or "model, version not recorded"


# ---------------------------------------------------------------------------
# citation checking
# ---------------------------------------------------------------------------
# Numbers only where they stand alone. Payment ids such as pay_5E72cODtQrmZkn
# contain digits, and matching those made the checker reject its own
# correctly-cited verdicts and fall back to the template every time.
_NUM = re.compile(r"(?<![A-Za-z0-9_.])\d[\d,]*(?:\.\d+)?(?![A-Za-z0-9_])")


def _variants(x: float) -> set:
    """String forms of a number that prose might reasonably use."""
    out = set()
    if x != x:
        return out
    for v in (x, round(x, 1), round(x, 2), round(x, 3), round(x, 4)):
        out.add(f"{v:g}")
    # Amounts print to the whole rupee, so 641.76 appears as "642".
    for n in (int(round(x)), int(x)):
        out.add(str(n))
        if abs(n) >= 1000:                              # 671235 and 6,71,235
            out.add(f"{n:,}")
            out.add(_indian_commas(n))
    return out


def _indian_commas(n: int) -> str:
    s = str(abs(n))
    if len(s) <= 3:
        head, tail = "", s
    else:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        head = ",".join(parts) + ","
    return ("-" if n < 0 else "") + head + tail


def allowed_numbers(ev: Evidence, d: Decision) -> set:
    """Numbers a verdict about this case may contain."""
    ok = set()
    for v in list(ev.local.values()) + list(ev.network.values()):
        ok |= _variants(float(v))

    amt = ev.amount_inr
    ok |= _variants(amt)
    ok |= _variants(amt / 1e5) | _variants(amt / 1e7)      # lakh, crore
    ok |= _variants(d.p_bad) | _variants(d.p_bad * 100)
    ok |= _variants(abs(d.ev_release_inr))
    ok |= _variants(float(ev.meta["threshold"]))

    tenure = float(ev.network["network_tenure_days"])
    ok |= _variants(tenure / 365.0)                        # "three years"
    ok |= _variants(tenure / 30.0)                         # "33 months"

    rate = float(ev.network["network_clean_rate"])
    ok |= _variants(rate * 100)                            # "100% clean"

    disputes = float(ev.network["network_disputes_prior"])
    orders = float(ev.network["network_orders_prior"])
    if orders:
        ok |= _variants(100.0 * disputes / orders)

    ok |= {"0", "1", "2", "3", "4", "5", "6"}              # counts in prose
    return ok


def check_citations(text: str, ev: Evidence, d: Decision) -> tuple:
    """Return (ok, tuple_of_unsupported_numbers)."""
    ok = allowed_numbers(ev, d)
    bad = []
    for raw in _NUM.findall(text):
        cleaned = raw.rstrip(".").replace(",", "")
        if not cleaned:
            continue
        if raw in ok or cleaned in ok:
            continue
        try:
            if f"{float(cleaned):g}" in ok:
                continue
        except ValueError:
            pass
        bad.append(raw)
    return (not bad), tuple(bad)


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------
def _brief(ev: Evidence, d: Decision) -> str:
    n = ev.network
    return f"""ORDER
  payment_id     {ev.payment_id}
  merchant       {ev.merchant}
  amount         Rs {ev.amount_inr:,.0f}
  method         {ev.meta['method']}
  blocked for    {ev.meta['block_reason']}
  merchant score {ev.local['risk_score']:.3f} against a threshold of {ev.meta['threshold']}

WHAT THE MERCHANT COULD SEE
  new device            {int(ev.local['f_device_is_new'])}
  address mismatch      {int(ev.local['f_address_mismatch'])}
  basket vs their norm  {ev.local['f_amount_z']:.2f} sd
  ordered at night      {int(ev.local['f_is_night'])}
  first order here      {int(ev.local['f_thin_file_flag'])}
  disposable email      {int(ev.local['f_disposable_email'])}
  pincode RTO index     {ev.local['f_pincode_rto_propensity']:.2f}
  prior RTO here        {int(ev.local['f_merchant_prior_rto'])}
  cash on delivery      {int(ev.local['f_is_cod'])}

WHAT ONLY THE NETWORK CAN SEE
  prior orders          {n['network_orders_prior']:.0f}
  across merchants      {n['network_merchants_prior']:.0f}
  tenure                {n['network_tenure_days']:.0f} days
  completed cleanly     {n['network_clean_rate']:.3f}
  prior disputes        {n['network_disputes_prior']:.0f}
  prior RTOs            {n['network_rto_prior']:.0f}
  devices seen          {n['network_device_fanout']:.0f}
  instrument used at    {n['network_instrument_merchants']:.0f} merchants

DECISION (already made, not yours to revisit)
  action                {d.action.value}
  P(block was correct)  {d.p_bad:.3f}
  expected value of release  Rs {d.ev_release_inr:,.0f}
  rules that fired      {', '.join(d.reasons)}"""


def _template(ev: Evidence, d: Decision) -> str:
    n = ev.network
    plural = lambda k, word: f"{n[k]:.0f} {word}{'' if n[k] == 1 else 's'}"
    file_ = (f"{plural('network_orders_prior', 'prior order')} across "
             f"{plural('network_merchants_prior', 'merchant')} over "
             f"{n['network_tenure_days']:.0f} days, "
             f"{n['network_clean_rate']:.3f} completed cleanly, "
             f"{plural('network_disputes_prior', 'dispute')}")
    flags = [name for name, key in (
        ("a new device", "f_device_is_new"),
        ("a shipping address that does not match billing", "f_address_mismatch"),
        ("an off-hours order", "f_is_night"),
        ("a first order at this merchant", "f_thin_file_flag"),
        ("a disposable email domain", "f_disposable_email"),
        ("a prior return at this merchant", "f_merchant_prior_rto"),
    ) if ev.local[key]]
    flag_text = ", ".join(flags) if flags else "no individual signal in isolation"

    head = {
        Action.OVERTURN: f"Released. {ev.merchant} blocked this Rs {ev.amount_inr:,.0f} order for {ev.meta['block_reason']}, and the network file does not support that.",
        Action.UPHOLD: f"Block upheld. {ev.merchant} blocked this Rs {ev.amount_inr:,.0f} order for {ev.meta['block_reason']}, and the network file agrees.",
        Action.STEP_UP: f"Held for verification. {ev.merchant} blocked this Rs {ev.amount_inr:,.0f} order for {ev.meta['block_reason']}, and the evidence does not settle it either way.",
        Action.ESCALATE: f"Referred to a reviewer. {ev.merchant} blocked this Rs {ev.amount_inr:,.0f} order for {ev.meta['block_reason']}, and there is not enough record to judge it automatically.",
    }[d.action]

    tail = {
        Action.OVERTURN: f"The signals that fired were {flag_text}, all consistent with this customer's prior behaviour.",
        Action.UPHOLD: f"The signals that fired were {flag_text}.",
        Action.STEP_UP: f"The signals that fired were {flag_text}. A short verification exchange would settle it.",
        Action.ESCALATE: f"The signals that fired were {flag_text}. A reviewer should look at this before anything is released.",
    }[d.action]

    return (f"{head} The customer's record across the network shows {file_}. "
            f"{tail} The model puts the probability that the block was correct at "
            f"{d.p_bad:.3f}.")


BRIEF_SYSTEM = """You write the hand-off note when an automated payments review
refuses to decide a case and sends it to a human.

The reviewer has thirty seconds and a queue. Give them, in this order and
nothing else:

1. What is being asked (release or keep blocked, the amount, the merchant).
2. What the record shows.
3. What is missing, which is why the case reached them.
4. What would settle it.

Four short paragraphs, no headings, under 120 words. Cite only figures in the
evidence. Do not recommend an outcome: if the system could pick one it would
not be sending the case over."""


def _brief_template(ev: Evidence, d: Decision) -> str:
    n = ev.network
    missing = []
    if n["network_orders_prior"] < 3:
        missing.append(f"only {n['network_orders_prior']:.0f} prior orders on the network")
    if n["network_tenure_days"] < 30:
        missing.append(f"the identity is {n['network_tenure_days']:.0f} days old")
    if not missing:
        missing.append("the evidence is balanced either way")
    return (
        f"For review: whether to release a Rs {ev.amount_inr:,.0f} order at "
        f"{ev.merchant}, blocked for {ev.meta['block_reason']}.\n\n"
        f"The record shows {n['network_orders_prior']:.0f} prior orders across "
        f"{n['network_merchants_prior']:.0f} merchants over "
        f"{n['network_tenure_days']:.0f} days, {n['network_clean_rate']:.3f} "
        f"completed cleanly, {n['network_disputes_prior']:.0f} disputes. The "
        f"merchant scored it {ev.local['risk_score']:.3f} against a threshold of "
        f"{ev.meta['threshold']}.\n\n"
        f"What is missing: {', and '.join(missing)}. The model puts the "
        f"probability the block was correct at {d.p_bad:.3f}, but on a file this "
        f"thin that number carries little weight, which is why this is here "
        f"rather than decided.\n\n"
        f"What would settle it: contact with the account holder confirming the "
        f"delivery address and a previous order, or converting the order to "
        f"prepaid."
    )


def write_brief(ev: Evidence, d: Decision, llm: LLMClient) -> Verdict:
    """Escalation hand-off note. Citation-checked like a verdict."""
    c: Completion = llm.complete(
        BRIEF_SYSTEM, [{"role": "user", "content": _brief(ev, d)}],
        max_tokens=700, effort="low", template=lambda: _brief_template(ev, d),
    )
    ok, bad = check_citations(c.text, ev, d)
    if ok:
        return Verdict(ev.payment_id, c.text.strip(), c.source, 1, True,
                       model=c.model)
    text = _brief_template(ev, d)
    ok2, bad2 = check_citations(text, ev, d)
    return Verdict(ev.payment_id, text, "template", 1, ok2, bad)


def write(ev: Evidence, d: Decision, llm: LLMClient) -> Verdict:
    """Generate, check, retry up to MAX_ATTEMPTS on a bad citation."""
    messages = [{"role": "user", "content": _brief(ev, d)}]
    last_bad: tuple = ()

    for attempt in range(1, MAX_ATTEMPTS + 1):
        msgs = list(messages)
        if last_bad:
            msgs.append({"role": "user", "content":
                         "Your previous note contained figures that are not in the "
                         f"evidence: {', '.join(last_bad)}. Rewrite it using only "
                         "figures shown above."})
        c: Completion = llm.complete(
            SYSTEM, msgs, max_tokens=600, effort="low",
            template=lambda: _template(ev, d),
        )
        ok, bad = check_citations(c.text, ev, d)
        if ok:
            return Verdict(ev.payment_id, c.text.strip(), c.source, attempt, True,
                           model=c.model)
        last_bad = bad

    # Fall back to the template, which is built from the evidence.
    text = _template(ev, d)
    ok, bad = check_citations(text, ev, d)
    return Verdict(ev.payment_id, text, "template", MAX_ATTEMPTS, ok, last_bad)
