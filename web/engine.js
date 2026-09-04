/* The shared engine: formatting, the fitted model, the recovery ladder and the
   live simulation. Injected into every page this project serves, at build time,
   from this one file.

   It lives here rather than in a template because there are now two front ends
   over the same numbers, and two copies of a transliterated model is
   the bug this project keeps catching in other people's work. Whatever
   web/check_dashboard.js and web/check_agent.js verify - 1,775 holdout
   probabilities against Python, 300 recovery plans against core/recovery.py -
   they verify for both, because there is only one of it.

   No DOM in here. Anything that reaches for an element belongs in a template. */
/* ===================================================================== utils */
const esc = (s) => String(s).replace(/[&<>"]/g, c =>
  ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;" }[c]));
const n0 = (x) => Math.round(x).toLocaleString("en-IN");
const pct = (x, d=1) => (100*x).toFixed(d) + "%";
function cr(x){
  const a = Math.abs(x), s = x < 0 ? "-" : "";
  if (a >= 1e7) return `${s}Rs ${(a/1e7).toFixed(2)} cr`;
  if (a >= 1e5) return `${s}Rs ${(a/1e5).toFixed(2)} L`;
  return `${s}Rs ${n0(a)}`;
}
/* An SMS costs 35 paise and cr() rounded that to "Rs 0", which made the
   cheapest channel on the page look free and the whole crossover argument
   look like it was about nothing. Anything under ten rupees keeps its paise. */
function rs(x){
  const a = Math.abs(x);
  if (a === 0) return "free";
  if (a < 10) return `${x < 0 ? "-" : ""}Rs ${a.toFixed(2)}`;
  return cr(x);
}
const C = { good:"#1a7f4b", bad:"#b3261e", warn:"#a16207", accent:"#2b4c7e",
            line:"#e2e0dc", mut:"#6b6b6b", faint:"#b6b2ab",
            // context grey for bars that are not the subject of the chart.
            // 21.5 dE from accent under protanopia and over 3:1 on white.
            ctx:"#8d8880",
            /* Sequential, for magnitude. One hue, light to dark, lightness
               strictly decreasing (0.446 / 0.205 / 0.072). Every bar drawn
               with it carries a direct label, which is what lets the lightest
               step sit under 3:1 on white. */
            seq:["#9fb4d0", "#5c7fae", "#2b4c7e"] };
const SANS = "ui-sans-serif,-apple-system,Segoe UI,Roboto,sans-serif";

/* Everything the deployment panel needs, from one frontier row.
   `drag` is the part that does not scale with the return rate: fraud booked in
   full plus dispute overhead plus review time. It is not carried per cap in the
   bundle, but it falls out of the row, because
       contribution = margin * recovered - drag
   so drag = margin*recovered - contribution, exactly. check_agent.js asserts
   this reproduces the Python queue arithmetic at the shipped cap, on every
   return rate, before anyone is allowed to believe the slider. */
function queueAt(row, margin){
  const gross = row.recovered;
  const drag = margin * gross - row.contribution;
  return { gross, drag,
           breakeven: gross > 0 ? drag / (margin * gross) : NaN,
           at: rate => margin * gross * rate - drag };
}


/* ============================================================ the real model */
/* Transliterated from web/export_bundle.py. The exporter refuses to write a
   bundle unless this reproduces the fitted Python model on every holdout case,
   and web/check_dashboard.js re-runs these exact functions pulled out of
   the built page, so what runs here is the model rather than a stand-in. */
function rawScore(x){
  let total = 0;
  for (const tree of B.model.trees){
    let n = tree;
    while (Array.isArray(n)) n = x[n[0]] <= n[1] ? n[2] : n[3];
    total += n;
  }
  return 1 / (1 + Math.exp(-total));
}
function calibrate(v){
  const xs = B.model.iso_x, ys = B.model.iso_y;
  if (v <= xs[0]) return ys[0];
  if (v >= xs[xs.length-1]) return ys[ys.length-1];
  let lo = 0, hi = xs.length - 1;
  while (hi - lo > 1){ const m = (lo+hi) >> 1; if (xs[m] <= v) lo = m; else hi = m; }
  const span = xs[hi] - xs[lo];
  return span === 0 ? ys[lo] : ys[lo] + (ys[hi]-ys[lo]) * (v-xs[lo]) / span;
}
function vectorise(o){
  const missing = [];
  const x = B.model.features.map(f => {
    let v = o[f];
    // `amount` is paise; amount_inr is rupees. Deriving one from the other
    // without the factor of 100 scores the order at 1/100th its size.
    if (v === undefined && f === "amount" && o.amount_inr != null)
      v = Number(o.amount_inr) * 100;
    if (v === undefined || v === null || v === "" || Number.isNaN(Number(v))){
      missing.push(f); return 0;
    }
    return Number(v);
  });
  return { x, missing };
}
/* core/policy.py decide(), same order of tests, same reason strings. */
function decide(p, amount, orders, tenure, cap){
  cap = cap ?? B.cap;
  const m = B.margin, f = B.dispute_overhead_inr;
  const ev = (1-p)*m*amount - p*(amount + f);
  const gate = [];
  if (orders < B.min_orders) gate.push(`thin_network_file(orders=${orders.toFixed(0)}<${B.min_orders})`);
  if (tenure < B.min_tenure_days) gate.push(`short_tenure(days=${tenure.toFixed(0)}<${B.min_tenure_days})`);
  const sufficient = gate.length === 0;
  const big = amount >= B.stepup_floor_inr;
  let action, reasons;
  if (p > B.uphold_floor){
    action = "UPHOLD";
    reasons = [`confidently_bad(p_bad=${p.toFixed(3)}>${B.uphold_floor})`];
    if (!sufficient) reasons = reasons.concat(["thin_file_but_uphold_needs_no_exculpation"], gate);
  } else if (!sufficient){
    action = big ? "STEP_UP" : "ESCALATE";
    reasons = ["insufficient_evidence"].concat(gate,
      [`amount_${big ? "above" : "below"}_stepup_floor`]);
  } else if (ev > 0 && p < cap){
    action = "OVERTURN";
    reasons = [`ev_positive(${ev>=0?"+":""}${n0(ev)})`, `p_bad_under_cap(${p.toFixed(3)}<${cap})`];
  } else {
    action = big ? "STEP_UP" : "UPHOLD";
    reasons = ["ambiguous", `ev=${ev>=0?"+":""}${n0(ev)}`, `p_bad=${p.toFixed(3)}`,
               `amount_${big ? "above" : "below"}_stepup_floor`];
  }
  return { action, ev, reasons, sufficient, gate };
}
const TONE = { OVERTURN:"good", UPHOLD:"bad", STEP_UP:"warn", ESCALATE:"warn" };

/* ============================================================ the recovery ladder */
/* Transliterated from core/recovery.py, the same way the model above is
   transliterated from the exporter. The bundle carries Python's plan for 300
   real holdout cases and web/check_agent.js replays every one of them
   through this code, so a page showing a different ladder than the numbers
   came from fails the build rather than the demo. */
const RC = B.recovery;
const CH = Object.fromEntries(RC.channels.map(c => [c.key, c]));
const RCFG = Object.assign({}, RC.cfg);

function rDecay(t, cfg){ return t <= 0 ? 1 : Math.exp(-Math.LN2 * t / cfg.half_life_min); }

function pReturn(c, elapsed, cfg, rung){
  const t = Math.max(0, elapsed) + c.latency;
  let p = c.reach * c.lift * rDecay(t, cfg);
  if (rung > 0) p *= Math.pow(1 - cfg.rung_correlation, rung);
  return Math.min(1, Math.max(0, p));
}
function ltvAtRisk(amount, orders, tenure, cfg){
  if (!cfg.count_ltv || orders < 3 || tenure < 90) return 0;
  return (orders / tenure) * cfg.ltv_horizon_days * amount * cfg.margin
         * cfg.churn_on_false_decline;
}
/* Which channels this action may use. A step-up is an unanswered question, so
   a one-way nudge cannot discharge it: that is a constraint on the channel,
   not a preference, and it is the only reason a voice agent is in the list. */
function allowedFor(action, inSession){
  if (action === "UPHOLD")   return [[], "block stands, nothing to recover"];
  if (action === "ESCALATE") return [[CH.human], "escalation is a person by definition"];
  if (action === "STEP_UP")
    return [RC.channels.filter(c => c.two_way),
            "a step-up is a question, so the channel has to take an answer"];
  if (inSession) return [[CH.auto], "still at checkout, so just let it through"];
  return [RC.channels.filter(c => c.key !== "auto"), "overturned, customer has left"];
}
function rPlan(action, evRelease, amount, orders, tenure, opts){
  opts = opts || {};
  const cfg = opts.cfg || RCFG;
  const elapsed = opts.elapsed || 0;
  const exclude = opts.exclude || [];
  let [pool, why] = allowedFor(action, !!opts.inSession);
  const reasons = [why];
  if (exclude.length){
    pool = pool.filter(c => exclude.indexOf(c.key) < 0);
    reasons.push(`unavailable(${exclude.slice().sort().join(",")})`);
  }
  if (opts.only) pool = pool.filter(c => c.key === opts.only);
  const V = evRelease + ltvAtRisk(amount, orders, tenure, cfg);
  // Whether to engage is already settled for two of the four actions. A
  // STEP_UP means the policy decided an exchange is warranted and an ESCALATE
  // means a person has to look, both because ev_release is NOT trustworthy on
  // that case - so re-testing ev_release to decide whether to bother is
  // circular, and it silently dropped three escalations on the floor.
  const committed = action === "STEP_UP" || action === "ESCALATE";
  const none = { action, value: V, rungs: [], p: 0, cost: 0, ev: 0, eta: 0, reasons };
  if (!pool.length) return none;
  if (V <= 0 && !committed){
    reasons.push(`conversion_worth_nothing(${Math.round(V)})`); return none; }

  const rungs = [], used = {};
  let remaining = 1, t = elapsed, spend = 0, etaNum = 0;
  for (let i = 0; i < cfg.max_rungs; i++){
    let best = null, bestEv = cfg.min_ev_inr || 0, bestP = 0;
    for (const c of pool){
      if (used[c.key]) continue;
      const p = pReturn(c, t, cfg, rungs.length);
      // `remaining` is the chance we are still trying: a rung only runs if
      // every earlier one missed, so its revenue and its cost both discount by it
      const ev = remaining * (p * V - c.cost);
      // an ordinary marketing floor. Without it, pure positive-EV greed put an
      // agentic phone call on a Rs 1,195 order for +Rs 1.83 of expected value
      if (c.cost > 0 && p * V < cfg.min_roas * c.cost) continue;
      if (ev > bestEv){ best = c; bestEv = ev; bestP = p; }
    }
    if (!best) break;
    rungs.push({ channel: best.key, at: t, cost: best.cost, p: bestP,
                 ev: bestEv, committed: false });
    used[best.key] = 1;
    spend += remaining * best.cost;
    etaNum += remaining * bestP * (t + best.latency);
    remaining *= (1 - bestP);
    t += Math.max(best.latency * 2, 30);
  }
  if (!rungs.length && committed){
    const c = pool.slice().sort((x, y) => x.cost - y.cost)[0];
    const p = pReturn(c, elapsed, cfg, 0);
    rungs.push({ channel: c.key, at: elapsed, cost: c.cost, p,
                 ev: p * V - c.cost, committed: true });
    reasons.push(`committed_${action.toLowerCase()}_floor(${c.key})`);
    spend = c.cost; remaining = 1 - p; etaNum = p * (elapsed + c.latency);
  }
  const pTotal = 1 - remaining;
  if (!rungs.length) reasons.push(`no_channel_pays_at(${Math.round(V)})`);
  return { action, value: V, rungs, p: pTotal, cost: spend,
           ev: rungs.reduce((a, r) => a + r.ev, 0),
           eta: pTotal > 0 ? etaNum / pTotal : 0, reasons };
}
function crossover(a, b, cfg){
  cfg = cfg || RCFG;
  const dp = pReturn(a, 0, cfg, 0) - pReturn(b, 0, cfg, 0);
  return dp <= 0 ? Infinity : (a.cost - b.cost) / dp;
}
const CHTONE = { auto:"good", sms:"blue", email:"neut", voice:"warn", human:"bad" };
const CHICON = { auto:"&#8987;", sms:"&#9993;", email:"&#64;", voice:"&#9742;", human:"&#128100;" };
function mins(m){
  if (m < 1) return "now";
  if (m < 60) return `${Math.round(m)} min`;
  if (m < 1440) return `${(m/60).toFixed(m < 600 ? 1 : 0)} h`;
  return `${(m/1440).toFixed(1)} d`;
}


/* --------------------------------------------------------------- 1. live --- */
/* A running instance rather than a screenshot of one.

   What is real: the cases, sampled from the 1,775 blocked orders in the
   holdout; the probability, which the model produced; the action, which the
   policy produced; the recovery ladder, which core/recovery.py produced; and
   the answer key, which is revealed only after a case resolves and is never an
   input to anything.

   What is simulated: arrival times, and the coin that decides whether a
   contacted customer actually came back. That coin is drawn against the
   declared per-channel probability, seeded, and the header says so. Whether a
   returning order turns out to be fraud is NOT a coin - it is the answer key. */
let LIVE = null;

function mulberry(seed){          // small deterministic PRNG, so a run repeats
  return function(){
    seed |= 0; seed = seed + 0x6D2B79F5 | 0;
    let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

function newRun(seed){
  const pool = B.recovery.check.slice();
  return {
    seed, rnd: mulberry(seed), t: 0, i: 0, pool,
    feed: [], inflight: [], done: [], closed: 0,
    // running totals, kept incrementally. `feed` and `done` are trimmed
    // for memory, so anything derived by walking them under-reports as
    // the run goes on. That is how the channel mix ended up
    // showing the last two hundred cases instead of all of them.
    mix: {}, retGood: 0, retBad: 0, returnedAmount: 0,
    n: 0, released: 0, upheld: 0, asked: 0, escalated: 0,
    recovered: 0, admitted: 0, spend: 0, lost: 0, returns: 0, etas: [],
    playing: true, speed: 30,        // virtual minutes per real second
  };
}

function send(run, key){
  run.spend += CH[key].cost;
  const m = run.mix[key] || (run.mix[key] = { n: 0, cost: 0 });
  m.n++; m.cost += CH[key].cost;
}

function liveTick(run, dtMin){
  run.t += dtMin;
  // arrivals: the holdout window ran 29 blocked orders a day, so one every
  // ~50 virtual minutes. Kept honest rather than sped up to look busy.
  while (run.t >= run.i * 50){
    // 7919 is coprime with the pool size, so this walks every case before
    // repeating any. The pool is 400 cases and the board keeps running past
    // that, so a long demo does see the same orders again.
    const c = run.pool[(run.i * 7919) % run.pool.length];
    run.i++;
    run.n++;
    const at = run.t;
    if (c.action === "OVERTURN") run.released++;
    else if (c.action === "UPHOLD") run.upheld++;
    else if (c.action === "STEP_UP") run.asked++;
    else run.escalated++;
    // one plan per case, computed here and used for sequencing, timing and
    // probability alike. The bundle's precomputed chain is what check_agent
    // compares this against; it is not a second source of truth at runtime.
    const plan = rPlan(c.action, c.ev, c.amount, c.orders, c.tenure);
    const item = { c, at, plan, rung: 0,
                   state: plan.rungs.length ? "contacting" : "closed",
                   fired: [], key: `${c.payment_id}_${run.n}` };
    run.feed.unshift(item);
    if (run.feed.length > 40) run.feed.pop();
    if (plan.rungs.length){
      run.inflight.push(item);
      send(run, plan.rungs[0].channel);
    } else {
      run.closed++;              // upheld: nothing to recover, nothing to spend
    }
  }
  // resolutions
  for (let k = run.inflight.length - 1; k >= 0; k--){
    const it = run.inflight[k], c = it.c;
    const g = it.plan.rungs[it.rung];
    const ch = CH[g.channel];
    const due = it.at + g.at + ch.latency;
    if (run.t < due) continue;
    it.fired.push(ch.key);
    if (run.rnd() < g.p){
      // they came back. Whether that was a good order is the answer key,
      // not another coin.
      it.state = "returned"; it.at_done = run.t;
      run.returns++;
      run.etas.push(run.t - it.at);
      run.returnedAmount += c.amount;
      // good or bad is the answer key, not a second coin
      if (c.good){ run.recovered += c.amount; run.retGood++; }
      else { run.admitted += c.amount; run.retBad++; }
      run.inflight.splice(k, 1);
      run.done.push(it);
    } else if (it.rung + 1 < it.plan.rungs.length){
      it.rung++;
      send(run, it.plan.rungs[it.rung].channel);
    } else {
      it.state = "lost"; it.at_done = run.t;
      run.lost++;
      run.inflight.splice(k, 1);
      run.done.push(it);
    }
  }
  if (run.done.length > 200) run.done.splice(0, run.done.length - 200);
}
