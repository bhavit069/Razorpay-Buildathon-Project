"""The verification exchange and the fact extraction that follows it.

When the file is too thin to judge, asking the customer a question produces
new information where re-reading the record cannot. Two model roles:

    agent     asks up to MAX_TURNS verification questions
    customer  answers, seeded from ground truth so honest customers answer
              and fraudsters evade

A third call extracts structured facts from the transcript. Only the facts
re-enter the pipeline; StepUpFacts has no field that names an action.

The customer simulator is the only place ground truth reaches the agent, and
it never touches adjudication.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from core.feature_store import Evidence
from core.policy import Verification

from .llm import Completion, LLMClient

MAX_TURNS = 3

AGENT_SYSTEM = """You are verifying a held payment for an Indian merchant.

The order was blocked and the file is too thin to clear it on record alone. Ask
short verification questions that a genuine account holder could answer easily
and someone using a stolen instrument could not.

Useful lines of questioning:
- confirm the delivery address in the customer's own words
- confirm a detail of a previous order on this account
- offer prepaid instead of cash on delivery

That last one matters. A refused cash-on-delivery order converted to prepaid
carries no return risk at all, so a customer who agrees to pay up front has
removed the exposure rather than argued about it.

One question per turn. Under 30 words. No greetings after the first turn. Never
state or imply a decision about the order."""

CUSTOMER_SYSTEM = """You are role-playing a customer whose order was held for
verification, in a text chat with the merchant.

Answer in character, in one or two sentences. Real people are terse, slightly
irritated at being asked, and do not write customer-service prose. Do not
break character or mention that you are playing a role."""

EXTRACT_SYSTEM = """Read the verification transcript and report only what the
customer demonstrably did.

Report facts, not impressions. If the customer gave a specific verifiable detail
without hedging, that is a confirmation. Vague agreement is not. Refusing or
deflecting is not. You are not judging whether they are honest and you are not
recommending anything."""

FACT_SCHEMA = {
    "type": "object",
    "properties": {
        "address_confirmed": {
            "type": "boolean",
            "description": "Customer restated the delivery address specifically and without hedging.",
        },
        "prior_order_confirmed": {
            "type": "boolean",
            "description": "Customer recalled a specific detail of an earlier order on the account.",
        },
        "prepaid_accepted": {
            "type": "boolean",
            "description": "Customer agreed to pay up front instead of cash on delivery.",
        },
        "evaded": {
            "type": "boolean",
            "description": "Customer deflected, refused, or gave answers that avoided the question.",
        },
        "notes": {"type": "string", "description": "One sentence, quoting the transcript."},
    },
    "required": ["address_confirmed", "prior_order_confirmed", "prepaid_accepted",
                 "evaded", "notes"],
    "additionalProperties": False,
}


MODEL_SOURCES = frozenset({"anthropic", "gemini", "cache"})


@dataclass
class StepUpFacts:
    address_confirmed: bool = False
    prior_order_confirmed: bool = False
    prepaid_accepted: bool = False
    evaded: bool = False
    notes: str = ""

    @property
    def confirmations(self) -> int:
        return sum((self.address_confirmed, self.prior_order_confirmed,
                    self.prepaid_accepted))

    def as_verification(self) -> Verification:
        """Turn the facts into the policy's verification input.

        A fixed table, not a model output, and the only channel from the
        conversation into the decision. See core.policy.Verification for why
        these facts do not go into the network features.
        """
        if self.evaded:
            return Verification()
        return Verification(
            identity_confirmed=self.address_confirmed or self.prior_order_confirmed,
            prepaid_accepted=self.prepaid_accepted,
            confirmations=self.confirmations,
        )


@dataclass
class StepUpResult:
    payment_id: str
    transcript: list = field(default_factory=list)
    facts: StepUpFacts = field(default_factory=StepUpFacts)
    verification: Verification = field(default_factory=Verification)
    source: str = "template"
    prepaid: bool = False


# ---------------------------------------------------------------------------
def _persona_brief(ev: Evidence, persona: str, outcome: str) -> str:
    n = ev.network
    honest = persona.startswith("legit")
    return f"""You ordered from {ev.merchant} for Rs {ev.amount_inr:,.0f} and it was held.

Your situation: {'you are a real customer and this is your own order' if honest else 'this is not your account and not your instrument'}.
Your record on this account: {n['network_orders_prior']:.0f} previous orders, {n['network_tenure_days']:.0f} days old.

{'Answer straightforwardly. You know your own address and what you have ordered before. You find the questions mildly annoying.' if honest else 'You cannot answer specifics about the real account holder. Deflect, claim to be in a hurry, complain about the delay, or give vague answers. Do not volunteer that anything is wrong.'}"""


def run(ev: Evidence, llm: LLMClient, persona: str = "legit_stable",
        outcome: str = "clean") -> StepUpResult:
    """One verification exchange. persona and outcome drive the simulated
    customer only; nothing downstream sees them."""
    res = StepUpResult(payment_id=ev.payment_id)
    transcript: list = []
    agent_msgs: list = []
    cust_msgs: list = []
    sources = set()

    opening = (f"Case {ev.payment_id}: Rs {ev.amount_inr:,.0f} at {ev.merchant}, "
               f"held for {ev.meta['block_reason']}. "
               f"{'Cash on delivery.' if ev.local['f_is_cod'] else 'Prepaid.'} "
               f"Network file: {ev.network['network_orders_prior']:.0f} prior orders, "
               f"{ev.network['network_tenure_days']:.0f} days. Begin verification.")
    agent_msgs.append({"role": "user", "content": opening})

    for turn in range(MAX_TURNS):
        q = llm.complete(AGENT_SYSTEM, agent_msgs, max_tokens=150, effort="low",
                         template=lambda t=turn: _AGENT_SCRIPT[t])
        sources.add(q.source)
        question = q.text.strip()
        transcript.append({"role": "agent", "text": question})
        agent_msgs.append({"role": "assistant", "content": question})

        cust_msgs.append({"role": "user", "content":
                          _persona_brief(ev, persona, outcome) if turn == 0 else question})
        if turn == 0:
            cust_msgs[0]["content"] += f"\n\nThe merchant asks: {question}"
        a = llm.complete(CUSTOMER_SYSTEM, cust_msgs, max_tokens=150, effort="low",
                         template=lambda t=turn: _customer_script(ev, persona, t))
        sources.add(a.source)
        answer = a.text.strip()
        transcript.append({"role": "customer", "text": answer})
        cust_msgs.append({"role": "assistant", "content": answer})
        agent_msgs.append({"role": "user", "content": answer})

    flat = "\n".join(f"{t['role']}: {t['text']}" for t in transcript)
    fx = llm.complete(
        EXTRACT_SYSTEM, [{"role": "user", "content": flat}],
        schema=FACT_SCHEMA, max_tokens=400, effort="low",
        template=lambda: json.dumps(_script_facts(persona)),
    )
    sources.add(fx.source)

    try:
        facts = StepUpFacts(**json.loads(fx.text))
    except (json.JSONDecodeError, TypeError):
        facts = StepUpFacts(notes="extraction failed; no facts credited")

    res.transcript = transcript
    res.facts = facts
    res.verification = facts.as_verification()
    res.prepaid = facts.prepaid_accepted and bool(ev.local["f_is_cod"])
    # "claude" was never one of the source strings a Completion can carry, so
    # this used to test against a value that never appeared and read "template"
    # for exchanges a model had actually written.
    res.source = "model" if sources & MODEL_SOURCES else "template"
    return res


# --- offline scripts --------------------------------------------------------
# Used when there is no cached exchange and no credentials.
_AGENT_SCRIPT = [
    "To release this order, please confirm the delivery address on the order, including the pincode.",
    "Thanks. Can you tell me roughly when you last ordered on this account, and what it was?",
    "Last thing: we can release it immediately if you pay up front instead of on delivery. Is that alright?",
]


def _customer_script(ev: Evidence, persona: str, turn: int) -> str:
    if persona.startswith("legit"):
        return [
            "Yes, it's the flat above the pharmacy, 4B, pincode 700029. Same as always.",
            "Couple of months back I think, a pair of headphones. Maybe March.",
            "Fine, I'll pay now. Just send the link.",
        ][turn]
    return [
        "It's the address on the order, can you just process it please.",
        "I don't remember, I order from a lot of places.",
        "I'd rather pay on delivery. Cancel it if that's a problem.",
    ][turn]


def _script_facts(persona: str) -> dict:
    if persona.startswith("legit"):
        return {"address_confirmed": True, "prior_order_confirmed": True,
                "prepaid_accepted": True, "evaded": False,
                "notes": "Customer gave a specific address and pincode and agreed to prepay."}
    return {"address_confirmed": False, "prior_order_confirmed": False,
            "prepaid_accepted": False, "evaded": True,
            "notes": "Customer declined to give specifics and refused prepayment."}
