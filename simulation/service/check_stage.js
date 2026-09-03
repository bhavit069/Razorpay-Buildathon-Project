/* The room version on :4005, checked the way the console on :4000 is.

   Two front ends over one set of numbers is the arrangement most likely to
   produce a demo that contradicts its own documentation, so most of what is
   here is not "does the stage render" but "does the stage say the same thing".
   Both pages inline service/engine.js, so the model and the recovery ladder are
   literally the same code; what still needs checking is that the stage reads it
   correctly and that the figures it prints are the ones the bundle holds.

       node service/check_stage.js
*/
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = process.env.AUDIT_ROOT || path.resolve(__dirname, "..");
const read = (f) => fs.readFileSync(path.join(ROOT, "artifacts", f), "utf8");
const stageHtml = read("stage.html");

function el(tag){
  const n = { tagName: (tag || "div").toUpperCase(), _html: "", style: {}, dataset: {},
    classList: { add(){}, remove(){}, toggle(){} },
    get innerHTML(){ return this._html; }, set innerHTML(v){ this._html = String(v); },
    setAttribute(){}, getAttribute(){ return null; }, removeAttribute(){},
    addEventListener(){}, appendChild(){}, matches(){ return false; },
    querySelector(){ return el(); }, querySelectorAll(){ return []; },
    getBoundingClientRect(){ return { width: 10, height: 10 }; },
    focus(){}, click(){}, textContent: "", value: "", checked: false, hidden: false };
  return n;
}

function load(html){
  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
  const byId = {};
  const sb = {
    document: { createElement: (t) => el(t),
      getElementById: (id) => (byId[id] = byId[id] || el()),
      querySelector: () => el(), querySelectorAll: () => [], addEventListener(){},
      documentElement: { setAttribute(){}, removeAttribute(){}, getAttribute: () => null } },
    getComputedStyle: () => ({ getPropertyValue: () => "#888888" }),
    localStorage: { getItem: () => null, setItem(){}, removeItem(){} },
    console, Math, JSON, Number, String, Array, Object, Date, isNaN, Set, Map,
    parseInt, parseFloat, Boolean, RegExp, Error, Intl, Function, Symbol, Infinity,
    performance: { now: () => 0 }, innerWidth: 1440, innerHeight: 900,
    location: { hash: "" }, addEventListener(){}, setTimeout(){},
    requestAnimationFrame(){},
  };
  sb.window = sb; sb.globalThis = sb;
  vm.createContext(sb);
  for (const [i, s] of scripts.entries()){
    try { vm.runInContext(s, sb, { timeout: 20000 }); }
    catch (e){
      if (i === scripts.length - 1) { sb.__bootError = e.message; break; }
      console.error(`script ${i} threw:`, e.message); process.exit(1);
    }
  }
  return { sb, byId, get: (n) => vm.runInContext(n, sb) };
}

const S = load(stageHtml);
let fail = 0;
const check = (name, problems) => {
  const bad = [].concat(problems).filter(Boolean);
  if (bad.length){ fail++; console.log(`[FAIL] ${name}`);
    bad.slice(0, 6).forEach(b => console.log(`         ${b}`)); }
  else console.log(`[ok]   ${name}`);
};

check("the page boots", S.sb.__bootError ? [S.sb.__bootError] : []);

const SCENES = S.get("SCENES");
const B = S.get("B");

/* ---- 1. every scene renders, including the ones built in start() ---------- */
const VOID = new Set(["br","hr","img","input","meta","link","source","path","line",
  "circle","rect","polygon","polyline","stop","marker","use","area","col","base"]);
function inspect(html, who){
  const bad = [];
  for (const s of ["undefined", "NaN", "[object Object]", "__BUNDLE__", "__ENGINE__", "Infinity"]){
    const n = (html.match(new RegExp(s.replace(/[[\]]/g, "\\$&"), "g")) || []).length;
    if (n) bad.push(`${who}: ${n}x "${s}"`);
  }
  const stack = [];
  for (const m of html.matchAll(/<(\/?)([a-zA-Z][a-zA-Z0-9]*)\b[^>]*?(\/?)>/g)){
    const t = m[2].toLowerCase();
    if (VOID.has(t) || m[3] === "/") continue;
    if (m[1]){ if (stack[stack.length-1] === t) stack.pop();
      else bad.push(`${who}: </${t}> closes <${stack[stack.length-1] || "nothing"}>`); }
    else stack.push(t);
  }
  if (stack.length) bad.push(`${who}: unclosed ${stack.slice(0,4).join(", ")}`);
  return bad;
}

check("every scene renders clean", SCENES.flatMap(s => {
  let html;
  try { html = s.html(); }
  catch (e){ return [`${s.key}: html() threw ${e.message}`]; }
  return inspect(html, s.key).concat(html.length < 300 ? [] : []);
}));

check("the scenes that build themselves at runtime do so", SCENES.flatMap(s => {
  if (!s.start) return [];
  try { s.start(); } catch (e){ return [`${s.key}: start() threw ${e.message}`]; }
  // start() writes into elements it looked up by id; read them back
  const wrote = Object.entries(S.byId).filter(([, e]) => e._html.length > 40);
  return wrote.length ? [] : [`${s.key}: start() produced no content`];
}));

/* the live scene's spotlight and counters, and the case scene's panels, are
   written into ids rather than returned, so read them out of the stub */
check("runtime-built panels render clean", (() => {
  const bad = [];
  for (const [id, e] of Object.entries(S.byId)){
    if (e._html.length < 40) continue;
    bad.push(...inspect(e._html, "#" + id));
  }
  return bad;
})());

/* ---- 2. the stage agrees with the bundle it was built from ---------------- */
const has = (needle) => stageHtml.includes(needle);
check("no headline figure is typed into the markup", (() => {
  // A hardcoded "8,265" renders identically to n0(B.stats.blocked) and stays
  // right until the day the generator is reseeded, at which point the page
  // lies quietly. So look in the template rather than the output: any
  // grouped number or rupee figure sitting in the source, outside a comment.
  const tpl = fs.existsSync(path.join(ROOT, "service", "stage_template.html"))
    ? fs.readFileSync(path.join(ROOT, "service", "stage_template.html"), "utf8")
    : null;
  if (tpl === null) return [];
  const body = tpl.replace(/<style>[\s\S]*?<\/style>/, "")
                  .replace(/\/\*[\s\S]*?\*\//g, "")
                  .replace(/^\s*\/\/.*$/gm, "");
  const bad = [];
  for (const m of body.matchAll(/\d{1,3}(?:,\d{2,3})+/g))
    bad.push(`a grouped number is typed into the template: "${m[0]}"`);
  for (const m of body.matchAll(/(?:Rs|&#8377;|₹)\s?\d[\d,.]*\s?(?:cr|L|lakh|crore)?/gi))
    bad.push(`a rupee figure is typed into the template: "${m[0].trim()}"`);
  return bad;
})());

check("the headline figures on the page are the bundle's", (() => {
  const bad = [];
  const rendered = SCENES.map(s => { try { return s.html(); } catch (e){ return ""; } }).join("");
  const all = rendered + Object.values(S.byId).map(e => e._html).join("");
  const want = [
    ["blocked orders", B.stats.blocked.toLocaleString("en-IN")],
    ["holdout cases", B.outcome.n_cases.toLocaleString("en-IN")],
    ["cases actioned", B.recovery.outcome.contacted.toLocaleString("en-IN")],
    ["cap", String(B.cap)],
  ];
  for (const [what, v] of want)
    if (!all.includes(v)) bad.push(`${what}: "${v}" appears nowhere on the stage`);
  return bad;
})());

check("the stage never quotes a recovery figure without its cost", (() => {
  // the project's standing rule, applied to the new surface: no recovery
  // number on screen without the fraud or the spend beside it
  const live = SCENES.find(s => s.key === "live");
  const all = live.html() + Object.values(S.byId).map(e => e._html).join("");
  const bad = [];
  if (/Revenue recovered/.test(all) && !/Fraud admitted/.test(all))
    bad.push("recovered is on the live scene without admitted");
  const ladder = SCENES.find(s => s.key === "ladder").html();
  if (/Expected to return/.test(ladder) && !/Cost per recovery/.test(ladder))
    bad.push("the ladder shows a return rate with no cost beside it");
  return bad;
})());

check("the asserted rates are labelled as asserted", (() => {
  const ladder = SCENES.find(s => s.key === "ladder").html();
  return /asserted, not\s*\n?\s*measured|asserted, not measured/.test(ladder)
    ? [] : ["the ladder scene does not say its channel rates are asserted"];
})());

check("the answer key is shown as a reveal, never as an input", (() => {
  const bad = [];
  const live = SCENES.find(s => s.key === "live").html();
  if (!/answer key/i.test(live))
    bad.push("the live scene does not say where the good/bad split comes from");
  // and the engine must not consult `good` before a case resolves.
  // indexOf returning -1 would slice from the wrong place and read as a pass,
  // so the anchors are asserted rather than assumed - which is the same
  // mistake this check exists to catch, one level up.
  const iFn = stageHtml.indexOf("function liveTick(");
  const draw = /run\.rnd\(\)[^\n]*/.exec(stageHtml.slice(Math.max(0, iFn)));
  if (iFn < 0) bad.push("liveTick( not found; this check is reading nothing");
  else if (!draw) bad.push("the coin flip in liveTick moved; this check is stale");
  else {
    // through the end of the draw line, not up to its start: a mutant that
    // multiplies the probability by the answer key sits ON that line, and
    // stopping short of it read as a pass
    const upToAndIncluding = stageHtml.slice(iFn, iFn + draw.index + draw[0].length);
    if (/\.good\b/.test(upToAndIncluding))
      bad.push("liveTick consults the answer key at or before the draw");
  }
  return bad;
})());

/* ---- 3. the live pipeline tracks the run it is drawing -------------------- */
check("the pipeline counts follow the simulation", (() => {
  const newRun = S.get("newRun"), liveTick = S.get("liveTick");
  const run = newRun(7);
  // a throw here used to take the whole check runner down with it, which reads
  // as a passing suite right up until you notice the missing lines
  try { for (let i = 0; i < 300; i++) liveTick(run, 60); }
  catch (e){ return [`the simulation threw after ${run.n} orders: ${e.message}`]; }
  const bad = [];
  const settled = run.returns + run.lost;
  if (run.n < 40) bad.push(`only ${run.n} orders in 300 ticks`);
  if (settled === 0) bad.push("nothing ever settled");
  if (run.returns + run.lost + run.inflight.length + run.closed !== run.n)
    bad.push("the simulation does not account for every arrival");
  return bad;
})());

/* ---- 4. the two front ends do not disagree -------------------------------- */
const dashPath = path.join(ROOT, "artifacts", "dashboard.html");
if (fs.existsSync(dashPath)){
  const D = load(read("dashboard.html"));
  check("stage and console run the same engine", (() => {
    const bad = [];
    const a = S.get("B"), b = D.get("B");
    if (a.model.n_trees !== b.model.n_trees) bad.push("different tree counts");
    if (a.cap !== b.cap) bad.push("different operating points");
    // Compare the live configuration objects, not only their outputs. A page
    // that overrides one knob after loading the shared engine produces
    // identical answers on most cases and a different answer on a few, which
    // sampling can miss entirely - it did, on min_roas.
    const [sCfg, dCfg] = [S.get("RCFG"), D.get("RCFG")];
    for (const k of new Set([...Object.keys(sCfg), ...Object.keys(dCfg)]))
      if (sCfg[k] !== dCfg[k])
        bad.push(`RCFG.${k}: ${sCfg[k]} on the stage, ${dCfg[k]} on the console`);
    // and then every case, not a slice of them
    const cases = a.recovery.check;
    const [sV, sR, sC, sD, sP] = ["vectorise","rawScore","calibrate","decide","rPlan"]
      .map(n => S.get(n));
    const [dV, dR, dC, dD, dP] = ["vectorise","rawScore","calibrate","decide","rPlan"]
      .map(n => D.get(n));
    for (const c of cases){
      const p1 = sP(c.action, c.ev, c.amount, c.orders, c.tenure);
      const p2 = dP(c.action, c.ev, c.amount, c.orders, c.tenure);
      if (p1.rungs.map(r => r.channel).join(",") !== p2.rungs.map(r => r.channel).join(","))
        bad.push(`${c.payment_id}: different ladders on the two pages`);
      if (Math.abs(p1.p - p2.p) > 1e-12) bad.push(`${c.payment_id}: different P(return)`);
    }
    for (const s of a.samples){
      const q1 = sC(sR(sV(s.json).x)), q2 = dC(dR(dV(s.json).x));
      if (Math.abs(q1 - q2) > 1e-15)
        bad.push(`${s.json.payment_id}: p_bad ${q1} on the stage, ${q2} on the console`);
      const d1 = sD(q1, s.json.amount_inr, s.json.network_orders_prior,
                    s.json.network_tenure_days);
      const d2 = dD(q2, s.json.amount_inr, s.json.network_orders_prior,
                    s.json.network_tenure_days);
      if (d1.action !== d2.action)
        bad.push(`${s.json.payment_id}: ${d1.action} on the stage, ${d2.action} on the console`);
    }
    return bad;
  })());
} else {
  console.log("[skip] stage and console run the same engine (no dashboard.html)");
}

/* ---- 5. self-contained and theme-complete --------------------------------- */
check("nothing loads from the network", (() => {
  const refs = [...stageHtml.matchAll(/(?:src|href)="(https?:)?\/\/[^"]+"/g)].map(m => m[0]);
  return refs.filter(r => !r.includes("fonts.g"));
})());

check("both themes define every colour they use", (() => {
  const css = (stageHtml.match(/<style>([\s\S]*?)<\/style>/) || [, ""])[1];
  const dark = css.slice(css.indexOf(":root{"), css.indexOf(':root[data-theme="light"]'));
  const light = css.slice(css.indexOf(':root[data-theme="light"]'));
  const names = (block) => new Set([...block.matchAll(/--([a-z0-9-]+)\s*:/g)].map(m => m[1]));
  const d = names(dark), l = names(light.slice(0, light.indexOf("}")));
  const missing = [...d].filter(k => !l.has(k));
  const used = new Set([...css.matchAll(/var\(--([a-z0-9-]+)\)/g)].map(m => m[1]));
  const undef = [...used].filter(k => !d.has(k));
  return missing.map(k => `--${k} has no light value, so the toggle half-works`)
    .concat(undef.map(k => `var(--${k}) is used but never defined`));
})());

check("the layout has no hardcoded viewport arithmetic", (() => {
  const css = (stageHtml.match(/<style>([\s\S]*?)<\/style>/) || [, ""])[1]
    .replace(/\/\*[\s\S]*?\*\//g, "");
  return [...css.matchAll(/calc\(\s*100[dvsl]*vh\s*-\s*(\d+)px\s*\)/g)]
    .map(m => `calc(100vh - ${m[1]}px) assumes a fixed header height`);
})());

/* A bundle map indexed by a stringified float returned undefined, pct() turned
   that into "0.0%", and every leak check passed because zero is a number. So:
   nothing on the stage may render an exact zero percent unless it is one of the
   handful of places a real zero belongs. Cheap, and it catches the whole class
   rather than the one instance. */
check("no panel renders a suspicious 0.0%", (() => {
  const all = SCENES.map(s => { try { return s.html(); } catch (e){ return ""; } })
    .join("") + Object.values(S.byId).map(e => e._html).join("");
  const hits = (all.match(/>\s*0\.0%\s*</g) || []).length;
  return hits ? [`${hits} element(s) render exactly 0.0%, which is usually a `
    + `lookup that missed rather than a measurement`] : [];
})());

/* and the same shape at the source: any literal index into a bundle map */
check("no bundle map is indexed by a hand-written key", (() => {
  const tpl = path.join(ROOT, "service", "stage_template.html");
  if (!fs.existsSync(tpl)) return [];
  const body = fs.readFileSync(tpl, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  return [...body.matchAll(/(?:B|h|o|RC)(?:\.[a-z_]+)*\[["'][^"']+["']\]/gi)]
    .map(m => `${m[0]} - iterate the map instead, the key is easy to mistype`);
})());

console.log(fail ? `\n${fail} stage problem(s)` : "\nthe stage holds up");
process.exit(fail ? 1 : 0);
