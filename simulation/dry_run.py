#!/usr/bin/env python3
"""Pre-demo dry run. Everything that happens on stage, with the network cut.

    python dry_run.py

The point is not that it passes. The point is that if it needs a code change to
pass, that is the finding, and it is better to find it here than in front of a
room. Run it twice; the second run has to be clean start to finish.

Network access is blocked for the duration by replacing socket.socket, so a
step that quietly reaches for an API fails loudly instead of working on this
machine and failing on the day.
"""
from __future__ import annotations

import io
import json
import os
import re
import socket
import subprocess
import sys
import time

PY = sys.executable
ROOT = os.path.dirname(os.path.abspath(__file__))
ENV = {**os.environ, "PYTHONUTF8": "1"}


class NetworkUsed(RuntimeError):
    pass


class _Blocked(socket.socket):
    def __init__(self, *a, **k):
        raise NetworkUsed("this step tried to open a socket; the demo runs off-network")


def cut_network():
    socket.socket = _Blocked
    socket.create_connection = lambda *a, **k: (_ for _ in ()).throw(
        NetworkUsed("this step tried to open a connection"))


class Check:
    def __init__(self):
        self.rows = []

    def __call__(self, name, fn):
        t0 = time.perf_counter()
        try:
            detail = fn() or ""
            ok = True
        except Exception as e:
            detail, ok = f"{type(e).__name__}: {e}", False
        ms = (time.perf_counter() - t0) * 1000
        self.rows.append((ok, name, detail, ms))
        mark = "pass" if ok else "FAIL"
        print(f"  [{mark}] {name:<46} {ms:7.0f} ms")
        if detail:
            for line in str(detail).splitlines():
                print(f"         {line}")
        return ok

    @property
    def failed(self):
        return [r for r in self.rows if not r[0]]


def sh(*args, timeout=600):
    """Subprocesses inherit a normal socket module, so anything that needs the
    network has to be caught by inspecting what it produced, not by the block
    above. Nothing here is supposed to need it."""
    r = subprocess.run([PY, *[str(a) for a in args]], cwd=ROOT, env=ENV,
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError(f"exit {r.returncode}: {(r.stderr or r.stdout)[-400:]}")
    return r.stdout


# ---------------------------------------------------------------------------
def main():
    print("RECLAIMIFY dry run, network cut\n")
    c = Check()

    # --- data and artifacts are where the demo expects them ----------------
    def data_present():
        need = ["data300k/appeal_queue.csv", "data300k/stats.json",
                "artifacts/headline.json", "artifacts/seed_check.json",
                "artifacts/case_room.html", "artifacts/tables.md", "METRICS.md"]
        missing = [p for p in need if not os.path.exists(os.path.join(ROOT, p))]
        if missing:
            raise FileNotFoundError(f"missing: {', '.join(missing)}")
        return f"{len(need)} required files present"
    c("required files present", data_present)

    # --- the docs are not stale --------------------------------------------
    def docs_current():
        r = subprocess.run([PY, "-m", "core.docs", "--check"], cwd=ROOT, env=ENV,
                           capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(r.stdout.strip() or "docs drifted")
        return "IDEA.md and DATA_CARD.md match the harness"
    c("generated doc blocks up to date", docs_current)

    def metrics_match_headline():
        h = json.load(open(os.path.join(ROOT, "artifacts/headline.json"), encoding="utf-8"))
        md = io.open(os.path.join(ROOT, "METRICS.md"), encoding="utf-8").read()
        want = {f"{h['recall']:.1%}", f"{h['precision']:.1%}",
                f"{h['abstention_rate']:.1%}"}
        missing = [w for w in want if w not in md]
        if missing:
            raise AssertionError(f"METRICS.md does not contain {missing}")
        return f"cap {h['cap']}, recall {h['recall']:.1%}, precision {h['precision']:.1%}"
    c("METRICS.md agrees with headline.json", metrics_match_headline)

    # --- the suite -----------------------------------------------------------
    def tests():
        out = sh("-m", "pytest", "tests", "-q")
        m = re.search(r"(\d+) passed", out)
        if not m:
            raise RuntimeError(out[-300:])
        return f"{m.group(1)} passed"
    c("test suite", tests)

    # --- now cut the network and run the demo path in-process ---------------
    cut_network()
    print("\n  network cut\n")

    from agent.llm import LLMClient
    from agent.orchestrator import Orchestrator
    from core.feature_store import FeatureStore
    from core.metrics import grade
    from core.model import Adjudicator
    from core.policy import PolicyConfig
    from core.showcase import pick as pick_showcase
    from core.truth import TruthVault

    state = {}

    def load():
        state["store"] = FeatureStore.load("data300k")
        state["vault"] = TruthVault("data300k")
        state["model"] = Adjudicator().fit(state["store"], state["vault"])
        return f"{len(state['store'])} cases, model fitted"
    if not c("load data and fit the model", load):
        return report(c)

    def showcase():
        picked = pick_showcase(state["store"], state["vault"], state["model"])
        state["picked"] = picked
        if len(picked) < 6:
            raise AssertionError(f"only {len(picked)} of 6 roles matched")
        for _, (e, _, _) in picked.items():
            if e.split != "holdout":
                raise AssertionError(f"{e.payment_id} is not a holdout case")
        return f"{len(picked)} roles matched, all holdout"
    c("six demo cases selected from holdout", showcase)

    def agent_run():
        store, vault, model = state["store"], state["vault"], state["model"]
        personas = {t.payment_id: (t.persona, t.true_outcome)
                    for t in vault.grade(store.payment_ids(store.split("holdout")))}
        orc = Orchestrator(store, model, PolicyConfig(cap=0.20),
                           LLMClient("offline"), None, personas)
        res = orc.run(store.split("holdout"), limit=300)
        orc.ledger.verify()
        state["orc"], state["res"] = orc, res
        bad = [r for r in res if not r.citations_ok]
        if bad:
            raise AssertionError(f"{len(bad)} verdicts failed the citation check")
        return (f"300 cases, ledger head {orc.ledger.head[:12]}, "
                f"all verdicts citation-clean")
    c("agent over 300 cases, off-network", agent_run)

    def matches_backtest():
        from core.backtest import run as backtest_run
        bt = {r.payment_id: r for r in backtest_run(
            state["store"], state["model"], PolicyConfig(cap=0.20), "holdout").records}
        drift = [r for r in state["res"] if r.p_bad != bt[r.payment_id].p_bad]
        if drift:
            raise AssertionError(f"{len(drift)} probabilities drifted from the backtest")
        diverged = [r for r in state["res"] if r.action != bt[r.payment_id].action]
        if not all(r.stepped_up for r in diverged):
            raise AssertionError("an action diverged without a step-up")
        return (f"0 of {len(state['res'])} probabilities drifted, "
                f"{len(diverged)} actions differ and all are step-ups")
    c("agent reproduces the backtest", matches_backtest)

    def priced():
        o = grade(state["orc"].as_core_ledger(state["res"]), state["vault"], None)
        return (f"precision {o.precision:.1%}, recall {o.recall_recoverable:.1%}, "
                f"fraud admitted Rs {o.fraud_admitted_inr/1e5:.2f} L")
    c("priced through the Phase 1 grader", priced)

    # --- the screen ----------------------------------------------------------
    def build_room():
        from service.case_room import build
        p = build(out="artifacts/case_room.html", data_dir="data300k", limit=400)
        html = io.open(os.path.join(ROOT, p), encoding="utf-8").read()
        cases = json.loads(re.search(r"const CASES=(\[.*?\]);", html, re.S).group(1))
        want = {e.payment_id for e, _, _ in state["picked"].values()}
        got = {c_["payment_id"] for c_ in cases}
        if not want <= got:
            raise AssertionError(f"demo cases missing from the page: {sorted(want - got)}")
        n_model = sum(c_["from_model"] for c_ in cases)
        demo_model = sum(1 for c_ in cases if c_["payment_id"] in want and c_["from_model"])
        if demo_model < len(want):
            raise AssertionError(
                f"only {demo_model} of {len(want)} demo cases carry model output; "
                f"run `python run.py warm`")
        for c_ in cases:
            if not c_["provenance"]:
                raise AssertionError(f"{c_['payment_id']} has no provenance string")
        state["room"] = (len(cases), n_model)
        size = os.path.getsize(os.path.join(ROOT, p)) / 1024
        return (f"{len(cases)} cases, {size:.0f} KB, all 6 demo cases present and "
                f"model-backed, {n_model} model verdicts total")
    c("case room builds with every demo case model-backed", build_room)

    def no_external_refs():
        html = io.open(os.path.join(ROOT, "artifacts/case_room.html"), encoding="utf-8").read()
        hits = re.findall(r"(?:src|href)=[\"']((?:https?:)?//[^\"']+)", html)
        if hits:
            raise AssertionError(f"page loads external resources: {hits[:3]}")
        return "no external src or href, opens from disk"
    c("case room is self-contained", no_external_refs)

    def build_console():
        """The console's agent page runs the exported model. The exporter
        already refuses to write a bundle that disagrees with Python, so
        getting here at all is most of the check; this confirms the page is
        built, self-contained, and carries every demo case."""
        import re

        from service.dashboard import build as build_console_page
        p = build_console_page(out="artifacts/dashboard.html", data_dir="data300k")
        html = io.open(os.path.join(ROOT, p), encoding="utf-8").read()
        refs = re.findall(r'(?:src|href)="((?:https?:)?//[^"]+)', html)
        outside = [r for r in refs if "fonts.g" not in r]
        if outside:
            raise AssertionError(f"console loads external resources: {outside[:3]}")
        b = json.loads(re.search(r"const B = (\{.*?\});\s*</script>", html, re.S).group(1))
        if len(b["samples"]) != 6:
            raise AssertionError(f"console ships {len(b['samples'])} demo cases, wanted 6")
        size = os.path.getsize(os.path.join(ROOT, p)) / 1024
        return (f"{size:.0f} KB, {b['model']['n_trees']} trees inlined, "
                f"{len(b['samples'])} demo cases, fonts are the only external ref")
    c("browser console builds and is self-contained", build_console)

    def responsive():
        """A static audit for the layout bugs that shipped once: a hardcoded
        header height, a grid floor wider than a phone, a wide diagram with no
        scroll container. Verified against the pre-fix page, where it finds
        five, so it is not a rubber stamp."""
        r = subprocess.run(["node", "service/check_responsive.js"], cwd=ROOT,
                           capture_output=True, text=True)
        if r.returncode:
            raise AssertionError(r.stdout.strip()[-400:])
        return f"{r.stdout.count('[ok]')} responsive checks pass"
    try:
        c("console layout is responsive", responsive)
    except FileNotFoundError:
        print("  [skip] console layout is responsive (no node on PATH)")

    def agent_page():
        """The agent page is the one a judge will actually poke at, and
        check_pages.js barely touches it: it calls render(), which emits an
        empty shell, and after(), which wires handlers to a stub DOM that
        no-ops. This drives the decision path itself over every case the page
        can load and reads the HTML back."""
        r = subprocess.run(["node", "service/check_agent.js"], cwd=ROOT,
                           capture_output=True, text=True)
        if r.returncode:
            raise AssertionError(r.stdout.strip()[-500:])
        return f"{r.stdout.count('[ok]')} agent checks pass over 12 cases"
    try:
        c("the agent page decides correctly", agent_page)
    except FileNotFoundError:
        print("  [skip] the agent page decides correctly (no node on PATH)")

    # --- replay strictness ---------------------------------------------------
    def replay_serves_demo_cases():
        store, vault, model = state["store"], state["vault"], state["model"]
        personas = {t.payment_id: (t.persona, t.true_outcome)
                    for t in vault.grade(store.payment_ids(store.split("holdout")))}
        orc = Orchestrator(store, model, PolicyConfig(cap=0.20),
                           LLMClient("replay"), None, personas)
        out = []
        for _, (e, _, _) in state["picked"].items():
            out.append(orc.handle(e))
        tmpl = [r.payment_id for r in out if not r.verdict_from_model]
        if tmpl:
            raise AssertionError(f"replay fell back to a template for {tmpl}")
        return f"{len(out)} demo cases served from cache with no network"
    c("replay mode serves the demo cases", replay_serves_demo_cases)

    return report(c)


def report(c):
    print()
    n = len(c.rows)
    if c.failed:
        print(f"{len(c.failed)} of {n} checks FAILED:")
        for _, name, detail, _ in c.failed:
            print(f"  - {name}: {detail}")
        print("\nA failure here is the finding. Fix it before the recording.")
        return 1
    print(f"{n} of {n} checks passed. Clean start to finish, network cut.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
