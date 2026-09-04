"""Getting the customer back, once the block has been overturned.

The decision core answers "was this block wrong". It stops there, and stopping
there recovers nothing: a reversed decision with no way to reach the customer
is a corrected row in a database. This is the second decision.

    which channel, at what cost, how long does it take, and is it worth it

It is the same expected-value shape as core.policy, applied to a different
question, and it composes with it rather than replacing it. `ev_release` is
already the value of a conversion, netting margin on a good order against the
whole basket plus dispute fee on a bad one. So:

    EV(channel) = P(return | channel, elapsed) * V  -  cost(channel)

where V is that conversion value. If the customer does not come back we spend
the contact cost and book nothing. So a cheap channel wins on small orders and
an expensive one has to earn its place.

WHY TIME IS IN HERE
-------------------
METRICS.md 11 reports the queue recontact rate as a band, 0.70 to 0.35, with
the note that "an in-session retry prompt is close to inline and a next-day
email is not". That band is this module in one number. A channel that reaches
the customer in two minutes and one that reaches them tomorrow are not the same
intervention, and the difference is most of the money. Decay is the mechanism:
a customer who was refused twenty minutes ago is still deciding, one refused
yesterday has already bought elsewhere.

WHAT IS ASSERTED, NOT MEASURED
------------------------------
Every per-channel number below. The dataset records payments, not outreach, so
it cannot supply a delivery rate, an answer rate or a conversion rate, and no
amount of care with the model conjures them. They are declared here in one
place, swept in METRICS, tunable on the console, and every figure derived from
them is labelled.

What does not depend on them is the crossover: the case value at which one
channel overtakes another. That is a threshold rather than a rate, it can be
checked against whatever a pilot actually measures, and it is the number to
quote. Same reason METRICS.md 11 leads with the 28.6% breakeven rather than
picking a point in the band.

Two assumptions push in the pessimistic direction and are called out where they
are made: rung independence is haircut because people who ignore an SMS
probably ignore a call too, and lifetime value at risk is off unless asked for.

Must not import core.truth.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .policy import Action

# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Channel:
    """One way of reaching a customer whose order we have decided to release.

    reach   share of attempts that land at all - delivered and opened, or
            answered in the case of a call
    lift    share of those reached who come back and complete, before decay
    two_way can it carry a question and take an answer? A one-way nudge cannot
            run a verification exchange, and STEP_UP needs one. That constraint,
            not persuasiveness, is why a voice call exists here
    """
    key: str
    name: str
    cost_inr: float
    latency_min: float
    two_way: bool
    reach: float
    lift: float
    note: str = ""

    @property
    def p_raw(self) -> float:
        """Conversion with no elapsed time. The best this channel ever does."""
        return self.reach * self.lift


# Ordered cheapest-and-fastest first, which is also the order the ladder tries
# them in. The anchors are chosen to agree with METRICS.md 11 rather than to
# flatter this module: AUTO is Deployment.inline() at 1.00, SMS lands on the
# 0.70 "in-session retry prompt" end of RECONTACT_RANGE and EMAIL on the 0.35
# "next-day email" end. If those disagreed, one of the two documents would be
# wrong. test_recovery.py asserts they do not.
CHANNELS = (
    Channel("auto", "Release in session", 0.0, 0.0, False, 1.00, 1.00,
            "The customer never sees a block. Nothing to recover."),
    Channel("sms", "SMS or WhatsApp", 0.35, 2.0, False, 0.96, 0.73,
            "Cheap, fast, one-way. Best conversion per rupee and cannot ask anything."),
    Channel("email", "Email", 0.08, 90.0, False, 0.55, 0.65,
            "A declined-payment email gets opened. It still converts half as well as "
            "an SMS for a fifth of the price, which is not a good enough trade to be "
            "anyone's first rung - it earns its place as a second one."),
    Channel("voice", "Agentic voice call", 14.00, 18.0, True, 0.35, 0.88,
            "Few people answer an unknown number, and the ones who do convert very "
            "well because the agent can finish the order on the line. An SMS nudges "
            "just as well. What a call buys is the cheapest channel that can ask a "
            "question and hear the answer, and that is the only reason it is here."),
    Channel("human", "Human callback", 150.00, 240.0, True, 0.72, 0.86,
            "The best conversion available and four hundred times the price of an "
            "SMS. Queues behind every other case a reviewer has."),
)
BY_KEY = {c.key: c for c in CHANNELS}
# The two ends of METRICS.md 11's frozen recontact band, restated here because
# the half-life is solved from them. Literals rather than an import, so this
# module keeps its distance from core.metrics, which reads the answer key.
RECONTACT_ANCHOR_TOP = 0.70        # "an in-session retry prompt"
RECONTACT_ANCHOR_BOTTOM = 0.35     # "a next-day email"
NO_CONTACT = "none"


# ---------------------------------------------------------------------------
# Configuration: everything below this line is asserted
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecoveryConfig:
    """Declared parameters. None of these is measured from the dataset.

    half_life_min
        How fast a winnable customer stops being winnable, because they have
        bought the thing somewhere else.

        This one is NOT a guess, which is worth being precise about since
        everything else here is. METRICS.md 11 already fixes two points on this
        curve from the frozen band: an in-session retry prompt at about two
        minutes recovers 0.70, and a next-day email at about 1440 minutes
        recovers 0.35. Exactly one exponential passes through both, and its
        half-life is 1438 minutes. Rounded to 1440, one day. If the frozen band
        is right then this is right, and if it is wrong then both are wrong
        together. That is how two documents should depend on each other.

    rung_correlation
        The ladder tries a second channel when the first does not convert, and
        treating those as independent events overstates it: someone who ignores
        an SMS is more likely than average to ignore a call. Each rung after
        the first is shrunk by this. 0 would be full independence, the
        optimistic lie. Declared at 0.45, erring pessimistic.

    count_ltv / churn_on_false_decline / ltv_horizon_days
        A wrongly refused customer may not just skip this order. The platform
        can see their whole history, so it can price the relationship, the one thing
        a single merchant cannot do. Off by default: it makes
        every channel look better and it rests on a churn rate nobody measured.
        The routing table is reported both ways.
    """
    half_life_min: float = 1440.0
    rung_correlation: float = 0.45
    margin: float = 0.25

    count_ltv: bool = False
    churn_on_false_decline: float = 0.11
    ltv_horizon_days: float = 365.0

    # A rung has to earn more than it costs, and by a margin. Pure positive-EV
    # greed put an agentic phone call on a Rs 1,195 pharmacy order because the
    # arithmetic came out at +Rs 1.83, which is true and is not a thing any
    # operations team would run. min_roas is the ordinary marketing floor:
    # expected revenue at least this multiple of spend, or do not send it.
    min_roas: float = 2.0
    min_ev_inr: float = 0.0
    max_rungs: int = 3

    # Human callbacks have a capacity, not just a price. METRICS.md 2 makes
    # exactly this point about the reviewer baseline - what beats a person is
    # reach, not judgment - and a recovery ladder that hands a reviewer
    # unlimited calls would rebuild the fantasy this project exists to
    # criticise. Rationed by case value per day, worst case falls to the next
    # channel down. At 29 blocked orders a day this does not bind; it is here
    # so that it does at the volume where it should.
    human_calls_per_day: int = 40
    human_minutes_per_call: float = 8.0

    def label(self) -> str:
        return (f"half-life {self.half_life_min:.0f} min, "
                f"rung correlation {self.rung_correlation:.2f}, "
                f"LTV {'on' if self.count_ltv else 'off'}")


def decay(elapsed_min: float, cfg: RecoveryConfig = RecoveryConfig()) -> float:
    """Share of the winnable customers still winnable after this long."""
    if elapsed_min <= 0:
        return 1.0
    return math.exp(-math.log(2.0) * elapsed_min / cfg.half_life_min)


def p_return(channel: Channel, elapsed_min: float,
             cfg: RecoveryConfig = RecoveryConfig(), rung: int = 0) -> float:
    """Probability this attempt brings the customer back.

    elapsed_min is measured from the block, not from the attempt, and the
    channel's own latency is added to it: an email queued now still arrives an
    hour and a half from now and decays for that long.
    """
    t = max(0.0, elapsed_min) + channel.latency_min
    p = channel.p_raw * decay(t, cfg)
    if rung > 0:
        p *= (1.0 - cfg.rung_correlation) ** rung
    return min(1.0, max(0.0, p))


# ---------------------------------------------------------------------------
# What a conversion is worth
# ---------------------------------------------------------------------------


def ltv_at_risk(amount_inr: float, orders_prior: float, tenure_days: float,
                cfg: RecoveryConfig) -> float:
    """Margin on the orders this customer would have placed and now may not.

    Their own observed rate, projected over the horizon, times the churn
    probability. A customer with 214 orders across four years is worth more to
    keep than a customer with three, and the network file is the only place
    that shows up. Returns 0 when the file is too thin to project from, rather
    than extrapolating off two data points.
    """
    if not cfg.count_ltv or orders_prior < 3 or tenure_days < 90:
        return 0.0
    per_day = orders_prior / tenure_days
    future_orders = per_day * cfg.ltv_horizon_days
    return future_orders * amount_inr * cfg.margin * cfg.churn_on_false_decline


def conversion_value(ev_release_inr: float, amount_inr: float,
                     orders_prior: float, tenure_days: float,
                     cfg: RecoveryConfig) -> float:
    """V: what getting this customer back is worth.

    ev_release_inr comes straight from core.policy and already nets margin on a
    good order against the full basket plus dispute fee on a bad one, so the
    fraud risk is priced once, here, and not double-counted.
    """
    return ev_release_inr + ltv_at_risk(amount_inr, orders_prior, tenure_days, cfg)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rung:
    channel: str
    at_min: float           # minutes after the block that this fires
    cost_inr: float
    p_return: float         # incremental, given every earlier rung missed
    ev_inr: float           # expected rupees this rung adds
    why: str
    # True when the policy mandated the contact and the ladder only picked the
    # channel. A mandated rung may run at negative expected value - that is
    # what "a person has to look at this" costs - and a chosen one may not.
    committed: bool = False


@dataclass(frozen=True)
class Plan:
    """The whole outreach sequence for one case."""
    payment_id: str
    action: str
    value_inr: float
    rungs: tuple
    p_total: float          # chance the customer comes back at all
    cost_expected_inr: float
    ev_inr: float
    eta_min: float          # expected minutes from block to money, given return
    reasons: tuple

    @property
    def contacted(self) -> bool:
        return bool(self.rungs)

    @property
    def first(self) -> str:
        return self.rungs[0].channel if self.rungs else NO_CONTACT


def _allowed(action: Action | str, in_session: bool) -> tuple:
    """Which channels this action may use, and why.

    A STEP_UP is an unanswered question, so a one-way nudge cannot discharge
    it - that is a hard constraint on the channel, not a preference. An
    ESCALATE is by definition a person. An UPHOLD has nothing to recover.
    """
    a = Action(action) if not isinstance(action, Action) else action
    if a is Action.UPHOLD:
        return (), "block stands, nothing to recover"
    if a is Action.ESCALATE:
        return (BY_KEY["human"],), "escalation is a person by definition"
    if a is Action.STEP_UP:
        return tuple(c for c in CHANNELS if c.two_way), \
            "a step-up is a question, so the channel has to take an answer"
    if in_session:
        return (BY_KEY["auto"],), "still at checkout, so just let it through"
    return tuple(c for c in CHANNELS if c.key != "auto"), "overturned, customer has left"


def plan(action: Action | str, ev_release_inr: float, amount_inr: float,
         orders_prior: float = 0.0, tenure_days: float = 0.0,
         elapsed_min: float = 0.0, in_session: bool = False,
         payment_id: str = "", cfg: RecoveryConfig = RecoveryConfig(),
         exclude: frozenset = frozenset(), only: str | None = None) -> Plan:
    """Build the outreach ladder for one decided case.

    Greedy by expected value, deliberately so: each rung is added
    only while the rupees it is expected to bring in exceed what it costs to
    send, given every earlier rung has already missed. That is what makes the
    ladder track severity without anyone writing a severity table - a Rs 1,195
    order stops after the SMS because a Rs 14 call cannot pay for itself
    against it, and a Rs 5 lakh one earns a human.
    """
    pool, why = _allowed(action, in_session)
    if exclude:
        pool = tuple(c for c in pool if c.key not in exclude)
        reasons_extra = (f"unavailable({','.join(sorted(exclude))})",)
    else:
        reasons_extra = ()
    if only is not None:
        pool = tuple(c for c in pool if c.key == only)
    V = conversion_value(ev_release_inr, amount_inr, orders_prior, tenure_days, cfg)
    reasons = [why, *reasons_extra]

    a = Action(action) if not isinstance(action, Action) else action
    # Whether to engage at all is already settled for two of the four actions,
    # and re-litigating it here was a mistake worth naming. A STEP_UP means the
    # policy has decided a verification exchange is warranted; an ESCALATE
    # means a person has to look. Both were issued precisely because
    # ev_release is not trustworthy on that case, so testing ev_release again
    # to decide whether to bother is circular - and it silently dropped three
    # escalations on the floor. That is the worst thing to do to a case whose
    # whole reason for existing is that nobody has looked at it yet.
    #
    # For those two the ladder chooses only WHICH channel. For an OVERTURN it
    # chooses whether as well, and can do that honestly because ev_release > 0
    # holds there by construction.
    committed = a in (Action.STEP_UP, Action.ESCALATE)

    if not pool:
        return Plan(payment_id, str(getattr(action, "value", action)), V, (),
                    0.0, 0.0, 0.0, 0.0, tuple(reasons))
    if V <= 0 and not committed:
        reasons.append(f"conversion_worth_nothing({V:+,.0f})")
        return Plan(payment_id, str(getattr(action, "value", action)), V, (),
                    0.0, 0.0, 0.0, 0.0, tuple(reasons))

    rungs, remaining, t, spend_ev, eta_num = [], 1.0, elapsed_min, 0.0, 0.0
    used = set()
    for i in range(cfg.max_rungs):
        best, best_ev, best_p = None, cfg.min_ev_inr, 0.0
        for c in pool:
            if c.key in used:
                continue
            p = p_return(c, t, cfg, rung=len(rungs))
            # `remaining` is the chance we are still trying at all: a rung only
            # gets to run if every earlier one missed, so both its revenue and
            # its cost are discounted by that.
            ev = remaining * (p * V - c.cost_inr)
            if c.cost_inr > 0 and p * V < cfg.min_roas * c.cost_inr:
                continue
            if ev > best_ev:
                best, best_ev, best_p = c, ev, p
        if best is None:
            break
        rungs.append(Rung(best.key, t, best.cost_inr, best_p, best_ev,
                          f"{best_p:.0%} x {V:,.0f} - {best.cost_inr:,.2f}"))
        used.add(best.key)
        spend_ev += remaining * best.cost_inr
        eta_num += remaining * best_p * (t + best.latency_min)
        remaining *= (1.0 - best_p)
        # the next rung waits for this one to have plainly not worked
        t += max(best.latency_min * 2.0, 30.0)

    if not rungs and committed:
        # nothing cleared the bar, but the policy already committed to an
        # exchange. Take the cheapest channel it is allowed to use and say so.
        c = min(pool, key=lambda x: x.cost_inr)
        p = p_return(c, elapsed_min, cfg, rung=0)
        rungs = [Rung(c.key, elapsed_min, c.cost_inr, p, p * V - c.cost_inr,
                      "committed by the policy, cheapest permitted channel",
                      committed=True)]
        reasons.append(f"committed_{a.value.lower()}_floor({c.key})")
        spend_ev, remaining = c.cost_inr, 1.0 - p
        eta_num = p * (elapsed_min + c.latency_min)

    p_total = 1.0 - remaining
    ev = sum(r.ev_inr for r in rungs)
    eta = eta_num / p_total if p_total > 0 else 0.0
    if not rungs:
        reasons.append(f"no_channel_pays_at({V:,.0f})")
    return Plan(payment_id, str(getattr(action, "value", action)), V,
                tuple(rungs), p_total, spend_ev, ev, eta, tuple(reasons))


# ---------------------------------------------------------------------------
# Crossovers: the part that does not depend on believing the rates
# ---------------------------------------------------------------------------


def crossover_value(a: Channel, b: Channel, cfg: RecoveryConfig,
                    elapsed_min: float = 0.0) -> float:
    """Case value at which channel `a` overtakes channel `b`.

    Solves  p_a*V - c_a = p_b*V - c_b  for V. Returns inf when the cheaper
    channel also converts better, which means the dearer one never wins on
    conversion alone and is only ever chosen for a capability the other lacks -
    the reason a voice call is in this list at all.
    """
    pa, pb = p_return(a, elapsed_min, cfg), p_return(b, elapsed_min, cfg)
    dp, dc = pa - pb, a.cost_inr - b.cost_inr
    if dp <= 0:
        return float("inf")
    return dc / dp


def crossover_table(cfg: RecoveryConfig = RecoveryConfig(),
                    elapsed_min: float = 0.0) -> list:
    """Every ordered pair, for the docs. Rows are (dearer, cheaper, value)."""
    out = []
    for a in CHANNELS:
        for b in CHANNELS:
            if a.key == b.key or a.cost_inr <= b.cost_inr:
                continue
            out.append((a.key, b.key, crossover_value(a, b, cfg, elapsed_min)))
    return out


def decay_curve(cfg: RecoveryConfig = RecoveryConfig(),
                points=(0, 15, 30, 60, 120, 240, 480, 1440)) -> list:
    """P(return) per channel against time since the block, for the console."""
    return [{"t": t, **{c.key: p_return(c, t, cfg) for c in CHANNELS}}
            for t in points]


# ---------------------------------------------------------------------------
# Grading the ladder over a whole ledger
# ---------------------------------------------------------------------------


@dataclass
class RecoveryOutcome:
    cases: int = 0
    contacted: int = 0
    uncontacted: int = 0
    spend_inr: float = 0.0
    expected_returns: float = 0.0
    value_reached_inr: float = 0.0      # V-weighted, expected
    by_channel: dict = None
    # expected returns / contacted. An average, so it FALLS when the ladder
    # reaches further down into marginal cases even as total returns rise.
    # Read it next to expected_returns, never on its own.
    blended_rate: float = 0.0
    median_eta_min: float = 0.0
    human_wanted: int = 0               # cases the ladder wanted a person for
    human_denied: int = 0               # ...and could not have one, out of capacity
    human_minutes: float = 0.0
    # An escalation has exactly one permitted channel. Deny it for capacity and
    # the case does not get handled more cheaply. It goes unhandled: no rung
    # fires and nobody looks at it. Counted separately from the cases the
    # ladder deliberately left alone, because they are not the same thing.
    stranded: int = 0
    # Blocked orders per day this staffing level supports before rationing
    # starts. The useful form of the capacity number: at 29 a day it does not
    # bind, and this says where it would.
    supports_per_day: float = 0.0
    days: int = 0

    def rows(self) -> list:
        return [{"channel": k, **v} for k, v in sorted(
            (self.by_channel or {}).items(), key=lambda kv: -kv[1]["cases"])]


def grade(ledger, cfg: RecoveryConfig = RecoveryConfig(),
          in_session: bool = False, only: str | None = None) -> RecoveryOutcome:
    """Run the ladder over every decided case and total it up.

    Expected values, not a simulation: no coin is flipped, so this is stable
    under reruns and comparable across configurations. The console's live board
    does sample outcomes, and says so on screen.

    `only` restricts every case to a single channel, which is how the
    single-touch deployments in METRICS.md 11 are reproduced from here.
    """
    out = RecoveryOutcome(by_channel={})
    etas = []

    # First pass, unrationed, to find who wants a person. Then ration by case
    # value within each day and replan the overflow without one.
    wants_human = {}
    for r in ledger.records:
        p0 = plan(r.action, r.ev_release_inr, r.amount_inr,
                  r.network_orders_prior, r.network_tenure_days,
                  elapsed_min=0.0, in_session=in_session,
                  payment_id=r.payment_id, cfg=cfg, only=only)
        if any(g.channel == "human" for g in p0.rungs):
            wants_human.setdefault(r.day, []).append((p0.value_inr, r.payment_id))
    denied = set()
    for day, rows in wants_human.items():
        rows.sort(reverse=True)
        out.human_wanted += len(rows)
        for _, pid in rows[cfg.human_calls_per_day:]:
            denied.add(pid)
    out.human_denied = len(denied)

    for r in ledger.records:
        p = plan(r.action, r.ev_release_inr, r.amount_inr,
                 r.network_orders_prior, r.network_tenure_days,
                 elapsed_min=0.0, in_session=in_session,
                 payment_id=r.payment_id, cfg=cfg, only=only,
                 exclude=frozenset({"human"}) if r.payment_id in denied else frozenset())
        out.cases += 1
        if not p.contacted:
            out.uncontacted += 1
            if r.payment_id in denied and r.action == Action.ESCALATE.value:
                out.stranded += 1
            continue
        out.contacted += 1
        out.spend_inr += p.cost_expected_inr
        out.expected_returns += p.p_total
        out.value_reached_inr += p.p_total * p.value_inr
        if p.eta_min > 0:
            etas.append(p.eta_min)
        for rung in p.rungs:
            b = out.by_channel.setdefault(
                rung.channel, {"cases": 0, "spend_inr": 0.0, "expected_returns": 0.0})
            b["cases"] += 1
            b["spend_inr"] += rung.cost_inr
            b["expected_returns"] += rung.p_return
    out.human_minutes = (out.by_channel.get("human", {}).get("cases", 0)
                         * cfg.human_minutes_per_call)
    days = len({r.day for r in ledger.records}) or 1
    out.days = days
    share_wanting_a_person = out.human_wanted / out.cases if out.cases else 0.0
    out.supports_per_day = (cfg.human_calls_per_day / share_wanting_a_person
                            if share_wanting_a_person else float("inf"))
    out.blended_rate = (out.expected_returns / out.contacted) if out.contacted else 0.0
    if etas:
        etas.sort()
        out.median_eta_min = etas[len(etas) // 2]
    return out


def sweep(ledger, half_lives=(360, 1440, 4320), correlations=(0.0, 0.45, 0.70)) -> list:
    """How much the blended rate moves when the two guesses move."""
    rows = []
    for h in half_lives:
        for rho in correlations:
            cfg = RecoveryConfig(half_life_min=h, rung_correlation=rho)
            o = grade(ledger, cfg)
            rows.append({"half_life_min": h, "rung_correlation": rho,
                         "blended_rate": o.blended_rate,
                         "spend_inr": o.spend_inr,
                         "median_eta_min": o.median_eta_min})
    return rows
