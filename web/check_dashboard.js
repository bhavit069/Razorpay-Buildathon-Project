/* Run the dashboard's own model and policy code against every holdout case.

   The exporter already checks its Python reference implementation. This checks
   the JavaScript that actually ships in the page, by pulling the functions out
   of the built HTML and running them, so the thing being verified is the thing
   a viewer executes.

       node web/check_dashboard.js
*/
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(ROOT, "demo", "console.html"), "utf8");

// pull the bundle and the model/policy block straight out of the page
const bm = html.match(/const B = (\{[\s\S]*?\});\s*<\/script>/);
if (!bm) { console.error("could not find the bundle in the page"); process.exit(1); }
const B = JSON.parse(bm[1]);

function grab(name, src) {
  const i = src.indexOf(`function ${name}(`);
  if (i < 0) throw new Error(`no function ${name} in the page`);
  let depth = 0, started = false;
  for (let j = i; j < src.length; j++) {
    if (src[j] === "{") { depth++; started = true; }
    else if (src[j] === "}") { depth--; if (started && depth === 0) return src.slice(i, j + 1); }
  }
  throw new Error(`unterminated ${name}`);
}

const n0 = (x) => Math.round(x).toLocaleString("en-IN");
const src = ["rawScore", "calibrate", "decide"].map(f => grab(f, html)).join("\n");
const mod = new Function("B", "n0", src + "\nreturn { rawScore, calibrate, decide };")(B, n0);

// --- 1. the model -----------------------------------------------------------
const cases = JSON.parse(fs.readFileSync(path.join(ROOT, "artifacts", "check_cases.json"), "utf8"));
let worstP = 0, worstId = null;
for (const c of cases) {
  const p = mod.calibrate(mod.rawScore(c.x));
  const d = Math.abs(p - c.p_bad);
  if (d > worstP) { worstP = d; worstId = c.payment_id; }
}
console.log(`model    : ${cases.length} holdout cases, worst |js - python| = ${worstP.toExponential(2)} (${worstId})`);

// --- 2. the policy ----------------------------------------------------------
let bad = 0, first = null;
for (const c of cases) {
  const p = mod.calibrate(mod.rawScore(c.x));
  const d = mod.decide(p, c.amount_inr, c.orders, c.tenure, B.cap);
  if (d.action !== c.action) { bad++; if (!first) first = { c, got: d.action }; }
}
console.log(`policy   : ${cases.length - bad} of ${cases.length} actions match the Python backtest`);
if (first) console.log(`  first mismatch ${first.c.payment_id}: python ${first.c.action}, js ${first.got}`);

// --- 3. the samples the agent page ships ------------------------------------
let sBad = 0;
for (const s of B.samples) {
  const x = B.model.features.map(f => {
    let v = s.json[f];
    if (v === undefined && f === "amount") v = s.json.amount_inr;
    return v === undefined ? 0 : Number(v);
  });
  const p = mod.calibrate(mod.rawScore(x));
  if (Math.abs(p - s.expected_p_bad) > 5e-7) {
    sBad++;
    console.log(`  sample ${s.json.payment_id}: expected ${s.expected_p_bad}, got ${p.toFixed(6)}`);
  }
}
console.log(`samples  : ${B.samples.length - sBad} of ${B.samples.length} agent-page samples reproduce their stored p_bad`);

const ok = worstP < 1e-9 && bad === 0 && sBad === 0;
console.log(ok ? "\nOK, the page runs the same model and the same policy as Python."
               : "\nFAILED");
process.exit(ok ? 0 : 1);
