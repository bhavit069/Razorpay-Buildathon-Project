/* Static responsive audit of the built console.

   There is no browser here to measure layout in, so this looks for the
   patterns that actually broke it, each one a bug that shipped:

     - a hardcoded viewport calc, `calc(100vh - 61px)`, which assumed a header
       height that stops being true the moment the header wraps
     - a grid column floor that cannot collapse, `minmax(330px, 1fr)` twice
       over, which forces a minimum width wider than a phone
     - an svg wider than a phone with no scroll container, so its labels get
       scaled down to nothing
     - a table with no overflow-x wrapper
     - an inline pixel height on a chart, which letterboxes it inside its card

       node service/check_responsive.js
*/
const fs = require("fs");
const path = require("path");

const ROOT = process.env.AUDIT_ROOT || path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(ROOT, "artifacts", "dashboard.html"), "utf8");
const css = (html.match(/<style>([\s\S]*?)<\/style>/) || [, ""])[1];
// strip comments so explanations of old bugs do not read as the bugs
const code = css.replace(/\/\*[\s\S]*?\*\//g, "");
const body = html.replace(/<style>[\s\S]*?<\/style>/, "");

const NARROW = 380;      // the phone this has to survive
let fail = 0;
const check = (name, problems) => {
  if (problems.length) {
    fail++;
    console.log(`[FAIL] ${name}`);
    problems.slice(0, 4).forEach(p => console.log(`         ${p}`));
  } else console.log(`[ok]   ${name}`);
};

// 1. no layout height derived from a guessed header size
check("no hardcoded viewport arithmetic",
  [...code.matchAll(/calc\(\s*100[dvsl]*vh\s*-\s*(\d+)px\s*\)/g)]
    .map(m => `calc(100vh - ${m[1]}px) assumes a fixed header height`));

// 2. every grid floor can collapse below a phone
check("grid columns collapse on a narrow screen",
  [...code.matchAll(/grid-template-columns\s*:\s*([^;}]+)/g)]
    .flatMap(m => {
      const decl = m[1];
      if (/min\(/.test(decl)) return [];          // already guarded
      const floors = [...decl.matchAll(/minmax\(\s*(\d+)px/g)].map(x => +x[1]);
      const fixed = [...decl.matchAll(/(?:^|\s)(\d+)px(?=\s|$)/g)].map(x => +x[1]);
      const total = [...floors, ...fixed].reduce((a, b) => a + b, 0);
      return total > NARROW ? [`${decl.trim()} floors at ${total}px`] : [];
    }));

// 3. inline styles in the markup are the ones that escape the media queries
check("no inline grid-template-columns in the markup",
  [...body.matchAll(/style="[^"]*grid-template-columns[^"]*"/g)].map(m => m[0].slice(0, 70)));

// 4. wide diagrams scroll rather than shrinking their labels away
check("wide svg has a scroll container",
  [...html.matchAll(/<svg[^>]*viewBox="0 0 (\d+) (\d+)"/g)]
    .filter(m => +m[1] > 700)
    .filter(m => {
      const before = html.slice(Math.max(0, m.index - 260), m.index);
      return !/class="[^"]*\bwide\b[^"]*"/.test(before);
    })
    .map(m => `viewBox width ${m[1]} with no .wide wrapper`));

// 5. charts fill their card instead of letterboxing. The charts are built by
// script at runtime, so the literal to look for is the template that emits
// them, not a rendered element: `style="height:${H}px"` never matches \d+px.
check("no inline pixel height on a chart",
  [...html.matchAll(/<svg class="chart"[^>]*style="[^"]*height:\s*(?:\d+|\$\{[^}]+\})px/g)]
    .map(m => m[0].slice(0, 76)));

// 6. tables can scroll sideways
const tables = [...html.matchAll(/<table[\s>]/g)];
check("every table sits in an overflow-x container",
  tables.filter(m => !/class="[^"]*\bscroll\b[^"]*"[^<]*$/
      .test(html.slice(Math.max(0, m.index - 200), m.index)))
    .map(m => `table at ${m.index} has no .scroll ancestor nearby`));

// 7. the breakpoints exist at all
const bps = [...code.matchAll(/@media\s*\(\s*max-width\s*:\s*(\d+)px/g)].map(m => +m[1]);
check("has breakpoints for tablet and phone",
  bps.length >= 2 && Math.min(...bps) <= 600 ? []
    : [`only found ${bps.length ? bps.join(", ") : "none"}`]);

// 8. nothing forces the body wider than the viewport
check("no fixed width wider than a phone on a block element",
  [...code.matchAll(/(?:^|[;{])\s*width\s*:\s*(\d{3,})px/g)]
    .filter(m => +m[1] > NARROW).map(m => `width:${m[1]}px`));

console.log(fail ? `\n${fail} responsive problem(s)` : "\nresponsive checks pass");
process.exit(fail ? 1 : 0);
