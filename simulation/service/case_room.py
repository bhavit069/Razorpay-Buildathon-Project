"""Generates the case room: one self-contained HTML file, no server.

ARCHITECTURE.md 2.6 asks for four screens. This is the one that cannot be cut,
and the review cut the other three: the notebooks and METRICS.md already carry
the frontier and the portfolio findings better than a dashboard would, and a
running server is one more thing to fail on stage.

Everything is inlined, so the file opens from disk with no network and no
build step.

    python -m service.case_room
"""
from __future__ import annotations

import html
import json
import os

from agent.llm import LLMClient
from agent.orchestrator import Orchestrator
from core.feature_store import FeatureStore
from core.model import Adjudicator
from core.policy import PolicyConfig
from core.showcase import ROLES, pick as pick_showcase
from core.truth import TruthVault

OUT = os.path.join("artifacts", "case_room.html")
CAP = 0.20

LOCAL_LABELS = {
    "f_device_is_new": "New device",
    "f_device_account_fanout": "Accounts on this device",
    "f_address_mismatch": "Shipping differs from billing",
    "f_orders_last_24h": "Orders in last 24h",
    "f_amount_z": "Basket vs merchant norm (sd)",
    "f_pincode_rto_propensity": "Pincode return index",
    "f_is_night": "Ordered at night",
    "f_thin_file_flag": "First order here",
    "f_disposable_email": "Disposable email",
    "f_international": "International",
    "f_merchant_prior_rto": "Prior return here",
    "f_is_cod": "Cash on delivery",
    "risk_score": "Merchant risk score",
}
NETWORK_LABELS = {
    "network_orders_prior": "Prior orders",
    "network_merchants_prior": "Across merchants",
    "network_tenure_days": "Tenure (days)",
    "network_clean_rate": "Completed cleanly",
    "network_disputes_prior": "Prior disputes",
    "network_rto_prior": "Prior returns",
    "network_device_fanout": "Devices seen",
    "network_instrument_merchants": "Instrument used at",
}
ACTION_TONE = {"OVERTURN": "good", "UPHOLD": "bad",
               "STEP_UP": "warn", "ESCALATE": "warn"}


def collect(data_dir="data300k", limit=400) -> list:
    store, vault = FeatureStore.load(data_dir), TruthVault(data_dir)
    model = Adjudicator().fit(store, vault)
    personas = {t.payment_id: (t.persona, t.true_outcome)
                for t in vault.grade(store.payment_ids(store.split("holdout")))}

    ho = store.split("holdout")
    picks = pick_showcase(store, vault, model)
    roles = {}
    for key, (e, p, v) in picks.items():
        roles.setdefault(e.payment_id, []).append(
            next(r.title for r in ROLES if r.key == key))

    # The showcase cases go first, then the stream up to the limit. Five of the
    # six sit past position 400 in chronological order, so taking a plain prefix
    # dropped them: the case room rendered no demo cases at all, and the three
    # recorded model verdicts never got requested, so every verdict on the page
    # was a template. Put them in explicitly rather than hoping they fall inside
    # the window.
    seen = set(roles)
    ordered = [e for e in ho if e.payment_id in roles]
    ordered += [e for e in ho if e.payment_id not in seen][:max(0, limit - len(ordered))]

    orc = Orchestrator(store, model, PolicyConfig(cap=CAP),
                       LLMClient("offline"), None, personas)
    results = orc.run(ordered)

    by_id = {e.payment_id: e for e in ho}
    truth = {v.payment_id: v for v in vault.grade([r.payment_id for r in results])}

    out = []
    for r in results:
        e = by_id[r.payment_id]
        out.append({
            "payment_id": r.payment_id,
            "merchant": e.merchant,
            "amount": e.amount_inr,
            "method": e.meta["method"],
            "block_reason": e.meta["block_reason"],
            "threshold": e.meta["threshold"],
            "action": r.action,
            "p_bad": r.p_bad,
            "verdict": r.verdict,
            "verdict_source": r.verdict_source,
            "provenance": r.verdict_provenance,
            "from_model": r.verdict_from_model,
            "brief": r.brief,
            "local": {k: e.local[k] for k in LOCAL_LABELS},
            "network": dict(e.network),
            "tools": r.tool_trace,
            "transcript": r.transcript,
            "truth": truth[r.payment_id].true_outcome,
            "roles": roles.get(r.payment_id, []),
        })
    # Showcase cases first, then by value.
    out.sort(key=lambda c: (not c["roles"], -c["amount"]))
    return out


CSS = """
:root{--bg:#faf9f7;--fg:#1a1a1a;--mut:#6b6b6b;--line:#e2e0dc;--card:#fff;
--good:#1a7f4b;--bad:#b3261e;--warn:#a16207;--accent:#2b4c7e}
*{box-sizing:border-box}
/* Flex column rather than three panes each subtracting a guessed header
   height. The header wraps to two or three lines on a narrow window, and
   calc(100vh - 61px) then made the panes taller than the space, which put a
   page scrollbar beside an inner one and cut off the bottom of the case. */
html{height:100%}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 ui-sans-serif,-apple-system,Segoe UI,Roboto,sans-serif;
display:flex;flex-direction:column;height:100dvh;overflow:hidden}
header{flex:none;padding:18px 24px;border-bottom:1px solid var(--line);
background:var(--card);display:flex;align-items:baseline;gap:16px;flex-wrap:wrap}
h1{font-size:17px;margin:0;font-weight:650;letter-spacing:-.01em}
.sub{color:var(--mut);font-size:13px}
.wrap{flex:1;min-height:0;display:grid;grid-template-columns:290px minmax(0,1fr)}
.list{border-right:1px solid var(--line);overflow-y:auto;min-height:0;
background:var(--card)}
.row{padding:10px 14px;border-bottom:1px solid var(--line);cursor:pointer}
.row:hover{background:#f4f2ef}
.row.sel{background:#eef2f8;box-shadow:inset 3px 0 0 var(--accent)}
.row .pid{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--mut)}
.row .amt{font-weight:600}
.tag{display:inline-block;font-size:10px;text-transform:uppercase;letter-spacing:.05em;
padding:1px 6px;border-radius:3px;font-weight:650}
.good{background:#e6f4ec;color:var(--good)}.bad{background:#fbeae9;color:var(--bad)}
.warn{background:#fdf4e3;color:var(--warn)}
.role{font-size:11px;color:var(--accent);margin-top:2px}
main{padding:24px 28px;overflow-y:auto;min-width:0;min-height:0}
.hd{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;
flex-wrap:wrap;margin-bottom:6px}
.hd h2{margin:0;font-size:22px;letter-spacing:-.02em}
.meta{color:var(--mut);font-size:13px;margin-bottom:18px}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(260px,100%),1fr));
gap:18px;margin:18px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:7px;padding:14px 16px}
.card h3{margin:0 0 10px;font-size:12px;text-transform:uppercase;letter-spacing:.06em;
color:var(--mut);font-weight:650}
table{width:100%;border-collapse:collapse;font-size:13px}
td{padding:3px 0;vertical-align:top}
td:last-child{text-align:right;font-variant-numeric:tabular-nums;font-weight:550}
.on{color:var(--bad);font-weight:650}.off{color:var(--mut)}
.verdict{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--accent);
border-radius:7px;padding:14px 16px;margin:16px 0;line-height:1.6}
.verdict.tmpl{border-left-color:var(--warn);background:#fffdf7}
.vhead{display:flex;align-items:center;gap:8px;margin:0 0 10px;font-size:12px;
text-transform:uppercase;letter-spacing:.06em;color:var(--mut);font-weight:650}
.prov{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:650;
padding:2px 8px;border-radius:999px;letter-spacing:.02em;text-transform:none}
.prov.model{background:#e6f4ec;color:var(--good);border:1px solid #b7e0c8}
.prov.tmpl{background:#fdf4e3;color:var(--warn);border:1px solid #ecd9a8}
.prov .dot{width:6px;height:6px;border-radius:50%;background:currentColor}
.src{font-size:11px;color:var(--mut);margin-top:10px;padding-top:8px;
border-top:1px dashed var(--line)}
.rowtag{font-size:9px;font-weight:700;letter-spacing:.06em;padding:0 4px;border-radius:2px;
vertical-align:1px}
.rowtag.tmpl{background:#fdf4e3;color:var(--warn)}
.rowtag.model{background:#e6f4ec;color:var(--good)}
pre{background:#f4f2ef;border:1px solid var(--line);border-radius:6px;padding:10px 12px;
overflow-x:auto;font:12px ui-monospace,Menlo,monospace;margin:0}
.chat div{margin-bottom:7px}.chat b{color:var(--accent)}
.foot{color:var(--mut);font-size:12px;margin-top:22px;border-top:1px solid var(--line);
padding-top:12px}
@media(max-width:1000px){.wrap{grid-template-columns:236px minmax(0,1fr)}
main{padding:20px 18px}}
@media(max-width:860px){
  body{height:auto;overflow:visible}
  .wrap{grid-template-columns:1fr}
  .list{border-right:0;border-bottom:1px solid var(--line);max-height:230px;
    position:sticky;top:0;z-index:20}
  main{overflow:visible;padding:20px 16px 48px}
  header{padding:14px 16px;gap:10px}
  .hd h2{font-size:19px}
}
@media(max-width:520px){
  .hd h2{font-size:17px}
  .card{padding:12px 13px}
  table.data{font-size:12px}
  pre{font-size:11px}
}
"""

JS = """
const T=document.getElementById('tpl');
function inr(n){return 'Rs '+Math.round(n).toLocaleString('en-IN')}
function rows(obj,labels,flags){let h='';for(const k in labels){const v=obj[k];
const isFlag=flags.includes(k);const on=isFlag&&v>0;
h+=`<tr><td class="${on?'on':(isFlag?'off':'')}">${labels[k]}</td><td>${
isFlag?(v>0?'yes':'no'):(Number.isInteger(v)?v:v.toFixed(k==='network_clean_rate'||k==='risk_score'?3:2))}</td></tr>`}
return h}
const FLAGS=['f_device_is_new','f_address_mismatch','f_is_night','f_thin_file_flag',
'f_disposable_email','f_international','f_merchant_prior_rto','f_is_cod'];
function show(i){
 const c=CASES[i];
 document.querySelectorAll('.row').forEach((r,j)=>r.classList.toggle('sel',j===i));
 let h=`<div class="hd"><div><h2>${inr(c.amount)} at ${c.merchant}</h2>
 <div class="meta"><code>${c.payment_id}</code> &middot; ${c.method} &middot;
 blocked for <b>${c.block_reason}</b> &middot; merchant scored
 ${c.local.risk_score.toFixed(3)} against a threshold of ${c.threshold}</div></div>
 <div style="text-align:right"><span class="tag ${c.tone}">${c.action}</span>
 <div class="meta" style="margin:6px 0 0">p_bad ${c.p_bad.toFixed(3)}<br>
 <span style="font-size:11px">actual outcome: ${c.truth}</span></div></div></div>`;
 if(c.roles.length)h+=`<div class="role" style="margin-bottom:12px">Demo role: ${c.roles.join(', ')}</div>`;
 h+=`<div class="cols">
 <div class="card"><h3>What the merchant could see</h3><table>${rows(c.local,LL,FLAGS)}</table></div>
 <div class="card"><h3>What only the network can see</h3><table>${rows(c.network,NL,[])}</table></div>
 </div>`;
 const pm=c.from_model;
 h+=`<div class="verdict${pm?'':' tmpl'}">
  <div class="vhead">Verdict
   <span class="prov ${pm?'model':'tmpl'}"><span class="dot"></span>${
     pm?'written by '+c.provenance:'TEMPLATE, not model output'}</span></div>
  ${c.verdict}
  <div class="src">${pm
    ? 'A language model wrote this text. Every figure in it was checked against the evidence below before it was shown. The model did not choose the action.'
    : 'No model wrote this. The text is filled in from the evidence by a fixed template, and is labelled so it cannot be mistaken for model output.'}
   <br>source: ${c.verdict_source}</div></div>`;
 if(c.brief)h+=`<div class="card"><h3>Escalation brief</h3>${
   c.brief.split('\\n\\n').map(p=>'<p style="margin:0 0 8px">'+p+'</p>').join('')}</div>`;
 if(c.transcript.length)h+=`<div class="card" style="margin-top:16px"><h3>Verification exchange</h3>
   <div class="chat">${c.transcript.map(t=>`<div><b>${t.role}</b>: ${t.text}</div>`).join('')}</div></div>`;
 h+=`<div class="card" style="margin-top:16px"><h3>Tools called</h3><pre>${
   c.tools.map(t=>t.tool.padEnd(16)+JSON.stringify(t.result)).join('\\n')}</pre></div>`;
 h+=`<div class="foot">Every number in the verdict above was checked against this
 evidence before it was shown. The model wrote the words; the decision came from
 the policy.</div>`;
 T.innerHTML=h;
}
"""


def build(out: str = OUT, data_dir: str = "data300k", limit: int = 400) -> str:
    cases = collect(data_dir, limit)
    for c in cases:
        c["tone"] = ACTION_TONE[c["action"]]

    listing = "".join(
        f'<div class="row" onclick="show({i})">'
        f'<div class="pid">{html.escape(c["payment_id"])}</div>'
        f'<div><span class="amt">Rs {c["amount"]:,.0f}</span> '
        f'<span class="tag {c["tone"]}">{c["action"]}</span> '
        f'<span class="rowtag {"model" if c["from_model"] else "tmpl"}">'
        f'{"MODEL" if c["from_model"] else "TMPL"}</span></div>'
        f'<div class="pid">{html.escape(c["merchant"])}</div>'
        + (f'<div class="role">{html.escape(c["roles"][0])}</div>' if c["roles"] else "")
        + "</div>"
        for i, c in enumerate(cases)
    )

    n_role = sum(1 for c in cases if c["roles"])
    n_model = sum(1 for c in cases if c["from_model"])
    models = sorted({c["provenance"] for c in cases if c["from_model"]})
    doc = f"""<!doctype html><meta charset="utf-8">
<title>Case room</title><style>{CSS}</style>
<header><h1>Case room</h1>
<span class="sub">{len(cases)} blocked orders reviewed at cap {CAP}.
{n_role} tagged as demo cases and listed first.<br>
<b>{n_model} of {len(cases)} verdicts were written by a model</b>
({", ".join(models) if models else "none"}); the remaining
{len(cases) - n_model} are deterministic templates, tagged
<span class="rowtag tmpl">TMPL</span> in the list and marked on the case.
Generated by <code>python -m service.case_room</code>.</span></header>
<div class="wrap"><div class="list">{listing}</div><main id="tpl"></main></div>
<script>
const CASES={json.dumps(cases)};
const LL={json.dumps(LOCAL_LABELS)};
const NL={json.dumps(NETWORK_LABELS)};
{JS}
show(0);
</script>"""

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return out


if __name__ == "__main__":
    p = build()
    print(f"wrote {p} ({os.path.getsize(p)/1024:.0f} KB)")
