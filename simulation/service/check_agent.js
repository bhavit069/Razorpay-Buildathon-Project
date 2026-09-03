/* Exercise the agent page's decision path in node.

   check_pages.js calls each page's render() and after(). For the agent page
   that proves almost nothing: render() emits an empty shell, and after() wires
   handlers against a stub DOM whose click() is a no-op, so the part a judge
   actually looks at - the verdict and the seven-step trace - never runs.

   This runs it. trace() and verdictHTML() are module-scope pure functions, so
   they can be driven over every scenario the page ships and the HTML read back:

     - every scenario reaches the action it is advertised as reaching
     - the trace agrees with decide(): same p, same action, same EV
     - the policy ladder fires exactly one rung, and it is the one whose
       output matches the action
     - the tree paths are real: every split is re-checked against the vector
     - the counterfactuals move the score in the direction claimed
     - amount in paise, so a case with amount_inr and no amount scores the same
       as the same case with both. This was wrong: the fallback passed rupees
       into a feature whose splits run from Rs 621 to Rs 3.86 lakh.
     - no undefined / NaN / unbalanced tags in any of the emitted HTML
     - every field renders a control

       node service/check_agent.js
*/
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = process.env.AUDIT_ROOT || path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(ROOT, "artifacts", "dashboard.html"), "utf8");
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);

function el(tag = "div"){
  return { tagName: tag.toUpperCase(), _html: "", style: {}, dataset: {},
    classList: { add(){}, remove(){}, toggle(){} },
    get innerHTML(){ return this._html; }, set innerHTML(v){ this._html = String(v); },
    setAttribute(){}, getAttribute(){ return null; }, addEventListener(){},
    appendChild(){}, querySelector(){ return el(); }, querySelectorAll(){ return []; },
    getBoundingClientRect(){ return { width: 10, height: 10 }; },
    focus(){}, click(){}, textContent: "", value: "", checked: false, hidden: false };
}
const seen = {};
const sandbox = {
  document: { createElement: (t) => el(t),
    getElementById: (id) => (seen[id] = seen[id] || el()),
    querySelector: () => el(), querySelectorAll: () => [], addEventListener(){} },
  console, Math, JSON, Number, String, Array, Object, Date, isNaN, Set, Map,
  parseInt, parseFloat, Boolean, RegExp, Error, Intl, Function, Symbol,
  innerWidth: 1440, innerHeight: 900, location: { hash: "" },
  performance: { now: () => 0 }, Infinity,
  addEventListener(){}, setTimeout(){}, requestAnimationFrame(){},
};
sandbox.window = sandbox; sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const [i, s] of scripts.entries()){
  try { vm.runInContext(s, sandbox, { timeout: 20000 }); }
  catch (e){ if (i === scripts.length - 1) break;
    console.error(`script ${i} threw:`, e.message); process.exit(1); }
}
const get = (n) => vm.runInContext(n, sandbox);
const pct = (x) => (100 * x).toFixed(1) + "%";
const { B, trace, verdictHTML, decide, vectorise, rawScore, calibrate,
        WRITTEN, FIELDS, fieldHTML, TONE, rPlan, pReturn, crossover, CH,
        newRun, liveTick } =
  Object.fromEntries(["B","trace","verdictHTML","decide","vectorise","rawScore",
    "calibrate","WRITTEN","FIELDS","fieldHTML","TONE","rPlan","pReturn",
    "crossover","CH","newRun","liveTick"].map(n => [n, get(n)]));

let fail = 0;
const check = (name, problems) => {
  const bad = [].concat(problems).filter(Boolean);
  if (bad.length){ fail++; console.log(`[FAIL] ${name}`);
    bad.slice(0, 6).forEach(b => console.log(`         ${b}`)); }
  else console.log(`[ok]   ${name}`);
};

/* the nine cases the page can load, and the action each is presented as */
const EXPECT = { pay_demo_wrongly_blocked:"OVERTURN", pay_demo_correctly_blocked:"UPHOLD",
                 pay_demo_no_record:"STEP_UP" };
/* the nine shipped cases do not between them reach every rung, so add three
   that do: a small thin file (ESCALATE), an ambiguous mid-score big order
   (STEP_UP from the last rung) and an ambiguous small one (UPHOLD from it). */
const clean = B.samples[0].json;
const EXTRA = [
  Object.assign({}, WRITTEN[2].json, { payment_id:"pay_x_small_thin", amount_inr:3000, amount:300000 }),
  Object.assign({}, clean, { payment_id:"pay_x_mid_big", network_clean_rate:0.62,
    network_disputes_prior:4, network_merchants_prior:5 }),
  Object.assign({}, clean, { payment_id:"pay_x_mid_small", amount_inr:1200, amount:120000,
    network_clean_rate:0.62, network_disputes_prior:4, network_merchants_prior:5 }),
];
const CASES = [...WRITTEN.map(w => w.json), ...B.samples.map(s => s.json), ...EXTRA];

/* 1. the written scenarios land where the copy says they land */
check("written scenarios reach the advertised action",
  WRITTEN.map(w => {
    const a = trace(w.json).d.action, want = EXPECT[w.json.payment_id];
    return a === want ? null : `${w.chip}: ${a}, page says ${want}`;
  }));

/* 2. the trace is the same computation the page scores with, not a retelling */
check("trace agrees with decide() on every case",
  CASES.map(j => {
    const r = trace(j);
    const p = calibrate(rawScore(vectorise(j).x));
    const amt = Number(j.amount_inr != null ? j.amount_inr : j.amount / 100);
    const d = decide(p, amt, Number(j.network_orders_prior || 0),
                     Number(j.network_tenure_days || 0));
    if (Math.abs(r.p - p) > 1e-12) return `${j.payment_id}: p ${r.p} vs ${p}`;
    if (r.d.action !== d.action) return `${j.payment_id}: ${r.d.action} vs ${d.action}`;
    if (Math.abs(r.d.ev - d.ev) > 1e-6) return `${j.payment_id}: EV ${r.d.ev} vs ${d.ev}`;
    return null;
  }));

/* 3. exactly one rung fires, and it is the one that produced the action */
check("the ladder fires exactly one rung, matching the action",
  CASES.map(j => {
    const r = trace(j), hit = r.rungs.filter(x => x.fired);
    if (hit.length !== 1) return `${j.payment_id}: ${hit.length} rungs fired`;
    if (hit[0].out !== r.d.action) return `${j.payment_id}: rung says ${hit[0].out}, action ${r.d.action}`;
    const after = r.rungs.slice(r.rungs.indexOf(hit[0]) + 1).some(x => x.reached);
    return after ? `${j.payment_id}: a rung below the fired one is marked reached` : null;
  }));

/* 4. the printed tree paths are the paths actually taken. Walk the tree here
      rather than following the branch the page printed, or a page that printed
      the wrong branch would just be followed into it. */
check("printed tree paths re-verify against the vector",
  CASES.flatMap(j => {
    const r = trace(j), idx = Object.fromEntries(B.model.features.map((f,i) => [f,i]));
    return r.paths.map((pp, t) => {
      let n = B.model.trees[t], k = 0;
      while (Array.isArray(n)){
        const s = pp.steps[k++];
        if (!s) return `${j.payment_id} tree ${t}: path ends at a split`;
        const f = B.model.features[n[0]], v = r.x[n[0]], left = v <= n[1];
        if (s.f !== f) return `${j.payment_id} tree ${t} step ${k}: printed ${s.f}, split is ${f}`;
        if (Math.abs(s.v - v) > 1e-12) return `${j.payment_id} tree ${t} step ${k}: printed ${s.v}, vector has ${v}`;
        if (Math.abs(s.thr - n[1]) > 1e-12) return `${j.payment_id} tree ${t} step ${k}: printed threshold ${s.thr}, tree has ${n[1]}`;
        if (s.left !== left) return `${j.payment_id} tree ${t} step ${k}: ${f} ${v} vs ${n[1]} goes ${left ? "left" : "right"}, printed ${s.left ? "left" : "right"}`;
        n = left ? n[2] : n[3];
      }
      if (k !== pp.steps.length) return `${j.payment_id} tree ${t}: ${pp.steps.length - k} extra printed steps`;
      return Math.abs(n - pp.leaf) > 1e-12 ? `${j.payment_id} tree ${t}: leaf ${pp.leaf} vs ${n}` : null;
    });
  }));

/* 5. each counterfactual really is that one field replaced */
check("counterfactuals reproduce when replayed",
  CASES.flatMap(j => {
    const r = trace(j), idx = Object.fromEntries(B.model.features.map((f,i) => [f,i]));
    return r.movers.slice(0, 6).map(m => {
      const y = r.x.slice(); y[idx[m.f]] = B.typical[m.f];
      const got = calibrate(rawScore(y)) - r.p;
      return Math.abs(got - m.d) < 1e-12 ? null
        : `${j.payment_id} ${m.f}: claims ${m.d.toFixed(6)}, replays ${got.toFixed(6)}`;
    });
  }));

/* 6. amount is paise. Deriving it from rupees without the factor of 100 scored
      the order at a hundredth of its size, silently. */
check("amount_inr alone gives the same score as amount_inr plus amount",
  CASES.map(j => {
    if (j.amount == null) return null;
    const bare = Object.assign({}, j); delete bare.amount;
    const a = trace(j), b = trace(bare);
    if (Math.abs(a.p - b.p) > 1e-12) return `${j.payment_id}: ${a.p} with amount, ${b.p} without`;
    const i = B.model.features.indexOf("amount");
    return Math.abs(b.x[i] - j.amount_inr * 100) > 1e-6
      ? `${j.payment_id}: derived amount ${b.x[i]}, expected ${j.amount_inr * 100} paise` : null;
  }));

/* 7. the HTML it emits */
const VOID = new Set(["br","hr","img","input","meta","link","source","path","line",
  "circle","rect","polygon","polyline","stop","marker","use","area","col","base"]);
function inspect(out, who){
  const problems = [];
  for (const bad of ["undefined", "NaN", "[object Object]", "Infinity"]){
    const n = (out.match(new RegExp(bad.replace(/[[\]]/g, "\\$&"), "g")) || []).length;
    if (n) problems.push(`${who}: ${n}x "${bad}"`);
  }
  const stack = [];
  for (const m of out.matchAll(/<(\/?)([a-zA-Z][a-zA-Z0-9]*)\b[^>]*?(\/?)>/g)){
    const t = m[2].toLowerCase();
    if (VOID.has(t) || m[3] === "/") continue;
    if (m[1]){ if (stack[stack.length-1] === t) stack.pop();
      else problems.push(`${who}: </${t}> closes <${stack[stack.length-1] || "nothing"}>`); }
    else stack.push(t);
  }
  if (stack.length) problems.push(`${who}: unclosed ${stack.slice(0,4).join(", ")}`);
  return problems;
}
check("the verdict renders clean for every case",
  CASES.flatMap(j => inspect(verdictHTML(j, null).html, j.payment_id)));

/* the delta badge only exists on a second run, so render each case twice */
check("the second run renders clean too",
  CASES.flatMap(j => inspect(verdictHTML(j, 0.5).html, j.payment_id + " (rerun)")));

/* 8. a hand-typed partial order still renders, and says what it assumed */
const partial = { payment_id:"pay_typed", merchant:"X", amount_inr:12000 };
check("a partial paste renders and declares its assumptions", (() => {
  const r = trace(partial), v = verdictHTML(partial, null).html;
  const bad = inspect(v, "partial");
  if (!r.missing.length) bad.push("nothing reported missing from a three-field order");
  if (!v.includes("treated as zero")) bad.push("the output does not say the gaps were assumed");
  return bad;
})());

/* 9. every field has a control, and the verdict tone matches the action */
check("every field renders a control",
  FIELDS.map(d => {
    const h = fieldHTML(d, d.kind === "text" || d.kind === "pick" ? "x" : 1);
    return /<(input|select)\b/.test(h) ? null : `${d.f} rendered no control`;
  }));
check("the verdict colour matches the action",
  CASES.map(j => {
    const r = trace(j), v = verdictHTML(j, null).html;
    return v.includes(`<span class="va ${TONE[r.d.action]}">${r.d.action}</span>`)
      ? null : `${j.payment_id}: no ${r.d.action} headline in ${TONE[r.d.action]}`;
  }));

/* 10. a check that only ever sees one rung fire is not checking the ladder.
       The third rung's `p < cap` is redundant with `EV > 0` at cap 0.20 -
       positive EV already implies p < m/(1+m) - so dropping it is an equivalent
       change at this operating point and no case can distinguish the two. */
const reached = new Set(CASES.map(j => trace(j).d.action));
check("the case set reaches every action",
  ["OVERTURN","UPHOLD","STEP_UP","ESCALATE"].filter(a => !reached.has(a))
    .map(a => `no case in the set produces ${a}, so that path is unchecked`));

/* 11. the recovery ladder in the page is the one core/recovery.py computed.
       Same standard the model is held to: the bundle carries Python's plan for
       300 real holdout cases and every one is replayed through the page's own
       code. A console that shows a different ladder than the numbers came from
       is a nicely typeset lie. */
check("the JS recovery ladder reproduces Python on 300 cases", (() => {
  const bad = [];
  let worst = 0;
  for (const c of B.recovery.check){
    const p = rPlan(c.action, c.ev, c.amount, c.orders, c.tenure);
    const got = p.rungs.map(r => r.channel).join(",");
    if (got !== c.chain.join(",")){
      bad.push(`${c.payment_id}: js [${got}] vs python [${c.chain.join(",")}]`);
      continue;
    }
    worst = Math.max(worst, Math.abs(p.p - c.p), Math.abs(p.cost - c.cost),
                     Math.abs(p.eta - c.eta), Math.abs(p.value - c.value));
  }
  // the bundle rounds to 9 places on the way out, so that is the floor
  if (worst > 1e-8) bad.push(`worst numeric gap ${worst.toExponential(2)}`);
  return bad;
})());

/* 12. the constraints, restated against the page's copy rather than Python's */
check("a step-up never gets a one-way channel", (() => {
  const bad = [];
  for (const c of B.recovery.check){
    if (c.action !== "STEP_UP") continue;
    for (const r of rPlan(c.action, c.ev, c.amount, c.orders, c.tenure).rungs)
      if (!CH[r.channel].two_way) bad.push(`${c.payment_id}: ${r.channel} cannot take an answer`);
  }
  return bad;
})());

check("an escalation goes to a person and an upheld block goes nowhere", (() => {
  const bad = [];
  for (const c of B.recovery.check){
    const r = rPlan(c.action, c.ev, c.amount, c.orders, c.tenure).rungs;
    if (c.action === "ESCALATE" && r.map(x => x.channel).join(",") !== "human")
      bad.push(`${c.payment_id}: escalation routed to ${r.map(x => x.channel) || "nothing"}`);
    if (c.action === "UPHOLD" && r.length)
      bad.push(`${c.payment_id}: upheld block still contacted`);
  }
  return bad;
})());

check("no rung the ladder CHOSE is sent at a loss", (() => {
  // A mandated rung may run at a loss: an escalation gets a reviewer whether
  // or not the arithmetic likes it, which is what escalating means. Those
  // carry a flag rather than being averaged in with the rest.
  const bad = [];
  for (const c of B.recovery.check)
    for (const r of rPlan(c.action, c.ev, c.amount, c.orders, c.tenure).rungs)
      if (!r.committed && !(r.ev > 0))
        bad.push(`${c.payment_id}: chose ${r.channel} at ev ${r.ev.toFixed(2)}`);
  return bad;
})());

check("a committed action is always actioned", (() => {
  const bad = [];
  for (const c of B.recovery.check){
    if (c.action !== "ESCALATE" && c.action !== "STEP_UP") continue;
    if (!rPlan(c.action, c.ev, c.amount, c.orders, c.tenure).rungs.length)
      bad.push(`${c.payment_id}: ${c.action} left with no channel at all`);
  }
  return bad;
})());

check("waiting costs money", (() => {
  const now = rPlan("OVERTURN", 100000, 500000, 40, 900, { elapsed: 0 });
  const later = rPlan("OVERTURN", 100000, 500000, 40, 900, { elapsed: 2880 });
  return later.p < now.p && later.ev < now.ev ? []
    : [`p ${now.p} -> ${later.p}, ev ${now.ev} -> ${later.ev} after two days`];
})());

check("the single-touch channels reproduce the frozen recontact band", (() => {
  // METRICS.md 11 calls 0.70 an in-session retry prompt and 0.35 a next-day
  // email. Those are the two anchors the half-life was solved from.
  const bad = [];
  const want = { sms: 0.70, email: 0.35 };
  for (const k of Object.keys(want))
    if (Math.abs(B.recovery.single_touch[k] - want[k]) > 0.02)
      bad.push(`${k}-only is ${B.recovery.single_touch[k].toFixed(3)}, band says ${want[k]}`);
  return bad;
})());

/* 17. the live board keeps its own running totals, on the most prominent
       screen in the console. If those double-count a return or lose a case
       between the feed and the tally, every number a judge looks at first is
       wrong and nothing else here would notice. */
check("the live board's books balance", (() => {
  const bad = [];
  const run = newRun(7);
  let prevRec = 0, prevAdm = 0, prevSpend = 0;
  for (let i = 0; i < 400; i++){
    liveTick(run, 60);
    if (run.recovered < prevRec) bad.push("recovered went down");
    if (run.admitted < prevAdm) bad.push("admitted went down");
    if (run.spend < prevSpend) bad.push("spend went down");
    prevRec = run.recovered; prevAdm = run.admitted; prevSpend = run.spend;
  }
  // every arrival is in exactly one place
  const accounted = run.returns + run.lost + run.inflight.length + run.closed;
  if (accounted !== run.n)
    bad.push(`${run.n} arrived but ${accounted} accounted for ` +
             `(${run.returns} returned, ${run.lost} lost, ` +
             `${run.inflight.length} in flight, ${run.closed} upheld)`);
  // the action tallies have to add up too
  const acts = run.released + run.upheld + run.asked + run.escalated;
  if (acts !== run.n) bad.push(`${run.n} arrived, ${acts} in the action tallies`);
  // an upheld block is never contacted and never costs anything
  if (run.closed !== run.upheld)
    bad.push(`${run.upheld} upheld but ${run.closed} closed without contact`);
  // spend must equal the channel mix the screen shows. Both are running
  // totals now: deriving either by walking `feed`/`done` under-reports,
  // because those are trimmed, which is exactly how the mix panel came to be
  // showing the last two hundred cases instead of the whole run.
  const mixCost = Object.values(run.mix).reduce((a, m) => a + m.cost, 0);
  if (Math.abs(mixCost - run.spend) > 1e-9)
    bad.push(`spend ${run.spend.toFixed(2)} but the mix totals ${mixCost.toFixed(2)}`);
  const mixN = Object.values(run.mix).reduce((a, m) => a + m.n, 0);
  if (mixN < run.returns) bad.push(`${run.returns} returned off ${mixN} attempts`);
  if (run.n < 50) bad.push(`only ${run.n} orders arrived in 400 ticks`);
  return bad;
})());

check("the live board is deterministic for a seed", (() => {
  const a = newRun(3), b = newRun(3);
  for (let i = 0; i < 200; i++){ liveTick(a, 60); liveTick(b, 60); }
  return (a.recovered === b.recovered && a.admitted === b.admitted
          && a.returns === b.returns && a.spend === b.spend)
    ? [] : [`same seed diverged: ${a.recovered} vs ${b.recovered}`];
})());

check("the board never books fraud as revenue", (() => {
  // whether a returning order was good comes from the answer key, not a coin
  const run = newRun(11);
  for (let i = 0; i < 400; i++) liveTick(run, 60);
  const bad = [];
  if (run.retGood + run.retBad !== run.returns)
    bad.push(`${run.returns} returned but ${run.retGood}+${run.retBad} classified`);
  if (Math.abs(run.recovered + run.admitted - run.returnedAmount) > 1e-6)
    bad.push(`recovered+admitted ${(run.recovered + run.admitted).toFixed(0)} ` +
             `vs value returned ${run.returnedAmount.toFixed(0)}`);
  if (run.retBad === 0) bad.push("no fraud came back at all, which is not credible");
  // and the split has to track the pool's own answer key, not a coin
  const poolBad = B.recovery.check.filter(c => c.good === false).length
                  / B.recovery.check.length;
  const seenBad = run.retBad / run.returns;
  if (seenBad > poolBad * 3) bad.push(`${pct(seenBad)} of returns were fraud ` +
    `against ${pct(poolBad)} of the pool - the split is not coming from the key`);
  return bad;
})());

console.log(fail ? `\n${fail} agent problem(s)` : "\nthe agent page decides correctly");
process.exit(fail ? 1 : 0);
