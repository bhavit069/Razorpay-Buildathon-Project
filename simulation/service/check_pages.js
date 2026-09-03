/* Render every dashboard page in node and inspect the HTML it produces.

   There is no headless browser here, so this is the substitute: run the page
   scripts against a stub DOM, call each render(), and fail on the things that
   silently look fine in a template literal and wrong on screen - a leaked
   `undefined`, a NaN, an unbalanced tag, an unresolved placeholder.

       node service/check_pages.js
*/
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(ROOT, "artifacts", "dashboard.html"), "utf8");

// everything between the first <script> and the last </script>, minus the tags
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
if (scripts.length < 3) { console.error("expected at least 3 script blocks"); process.exit(1); }

/* ---- the smallest DOM these scripts touch ---- */
function el(tag = "div") {
  const node = {
    tagName: tag.toUpperCase(), _html: "", children: [], style: {}, dataset: {},
    classList: { add(){}, remove(){}, toggle(){} },
    get innerHTML(){ return this._html; },
    set innerHTML(v){ this._html = String(v); },
    setAttribute(){}, getAttribute(){ return null; }, removeAttribute(){},
    addEventListener(){}, appendChild(){}, scrollIntoView(){},
    querySelector(){ return el(); },
    querySelectorAll(){ return []; },
    getBoundingClientRect(){ return { width: 10, height: 10 }; },
    focus(){}, click(){}, textContent: "", value: "", checked: false,
  };
  return node;
}
const captured = {};
const document = {
  createElement: (t) => {
    const n = el(t);
    if (t === "template") Object.defineProperty(n, "content", {
      get(){ return { firstElementChild: el() }; } });
    return n;
  },
  getElementById: (id) => (captured[id] = captured[id] || el()),
  querySelector: () => el(),
  querySelectorAll: () => [],
  addEventListener(){},
};
const sandbox = {
  document, console, Math, JSON, Number, String, Array, Object, Date, isNaN,
  parseInt, parseFloat, Boolean, RegExp, Error, Intl, Function, Symbol,
  innerWidth: 1440, innerHeight: 900, location: { hash: "" },
  performance: { now: () => 0 }, Infinity,
  addEventListener(){}, scrollTo(){}, setTimeout(){}, requestAnimationFrame(){},
};
sandbox.window = sandbox; sandbox.globalThis = sandbox;
vm.createContext(sandbox);

for (const [i, s] of scripts.entries()) {
  try { vm.runInContext(s, sandbox, { timeout: 20000 }); }
  catch (e) {
    // the last block boots the UI against a real DOM; a stub failing there is
    // expected and is not what this check is about.
    if (i === scripts.length - 1) { console.log(`(boot block skipped: ${e.message})`); break; }
    console.error(`script ${i} threw while loading:`, e.message); process.exit(1);
  }
}

// top-level `const` lives in the context's lexical scope, not on the sandbox
// object, so ask the context for it rather than reading a property.
let PAGES;
try { PAGES = vm.runInContext("PAGES", sandbox); }
catch (e) { console.error("PAGES not reachable:", e.message); process.exit(1); }
if (!PAGES || !PAGES.length) { console.error("no PAGES built"); process.exit(1); }

/* ---- inspect each page ---- */
const VOID = new Set(["br","hr","img","input","meta","link","source","path","line",
  "circle","rect","polygon","polyline","stop","marker","use","area","col","base"]);
let fail = 0;

for (const p of PAGES) {
  let out;
  try { out = p.render(); }
  catch (e) { console.log(`[FAIL] ${p.id.padEnd(10)} render threw: ${e.message}`); fail++; continue; }

  const problems = [];
  for (const bad of ["undefined", "NaN", "[object Object]", "__BUNDLE__", "Infinity"]) {
    const n = (out.match(new RegExp(bad.replace(/[[\]]/g, "\\$&"), "g")) || []).length;
    if (n) problems.push(`${n}x "${bad}"`);
  }
  // tag balance, ignoring void elements and self-closing forms
  const stack = [];
  for (const m of out.matchAll(/<(\/?)([a-zA-Z][a-zA-Z0-9]*)\b[^>]*?(\/?)>/g)) {
    const [, close, tag, self] = m;
    const t = tag.toLowerCase();
    if (VOID.has(t) || self === "/") continue;
    if (close) {
      if (stack[stack.length - 1] === t) stack.pop();
      else problems.push(`</${t}> closes <${stack[stack.length-1] || "nothing"}>`);
    } else stack.push(t);
  }
  if (stack.length) problems.push(`unclosed: ${stack.slice(0, 4).join(", ")}`);

  if (problems.length) { console.log(`[FAIL] ${p.id.padEnd(10)} ${problems.join(" | ")}`); fail++; }
  else console.log(`[ok]   ${p.id.padEnd(10)} ${String(out.length).padStart(6)} chars, tags balanced, no leaked values`);

  if (p.after) {
    try { p.after(); }
    catch (e) { console.log(`[FAIL] ${p.id.padEnd(10)} after() threw: ${e.message}`); fail++; }
  }
}

console.log(fail ? `\n${fail} page problem(s)` : "\nall pages render clean");
process.exit(fail ? 1 : 0);
