"""The queue haircut and the rationed reviewer.

Both were added after review, and both are the kind of arithmetic that is easy
to get wrong in a way nobody notices until it is on a slide. The queue discount
in particular has to be applied exactly once.
"""
import json
import os

import numpy as np
import pytest

from core.backtest import run
from core.metrics import (HUMAN_ACCURACY, HUMAN_COVERAGE, RECONTACT_RANGE,
                          REVIEW_COST_INR, Deployment, StepUpModel,
                          baseline_human_rationed, baseline_human_reviewer,
                          grade, recontact_arithmetic)
from core.policy import Action, PolicyConfig

CAP = 0.20


@pytest.fixture(scope="module")
def ledger(store, model):
    return run(store, model, PolicyConfig(cap=CAP), "holdout")


@pytest.fixture(scope="module")
def stepup():
    return StepUpModel()


def test_recontact_discount_is_applied_once(ledger, vault, stepup):
    """Net contribution must be margin on the already-discounted recovery, not
    a second haircut on top of it."""
    inline = grade(ledger, vault, stepup, deployment=Deployment.inline())
    for rate in RECONTACT_RANGE:
        q = grade(ledger, vault, stepup, deployment=Deployment.queue(rate))
        assert q.recovered_inr == pytest.approx(inline.recovered_inr * rate)
        fixed = inline.net_contribution_inr - ledger.config.margin * inline.recovered_inr
        expected = ledger.config.margin * inline.recovered_inr * rate + fixed
        assert q.net_contribution_inr == pytest.approx(expected)


def test_fraud_admitted_does_not_move_with_recontact(ledger, vault, stepup):
    inline = grade(ledger, vault, stepup, deployment=Deployment.inline())
    for rate in RECONTACT_RANGE:
        q = grade(ledger, vault, stepup, deployment=Deployment.queue(rate))
        assert q.fraud_admitted_inr == inline.fraud_admitted_inr
        assert q.n_released == inline.n_released


def test_published_arithmetic_reproduces_grade(ledger, vault, stepup):
    """Every row of the table in METRICS.md 11 has to come out of grade()."""
    ra = recontact_arithmetic(ledger, vault, stepup)
    for row in ra["rows"]:
        dep = (Deployment.inline() if row["rate"] == 1.0
               else Deployment.queue(row["rate"]))
        g = grade(ledger, vault, stepup, deployment=dep)
        assert row["net_contribution_inr"] == pytest.approx(g.net_contribution_inr)


def test_breakeven_rate_is_where_contribution_is_zero(ledger, vault, stepup):
    ra = recontact_arithmetic(ledger, vault, stepup)
    r = ra["breakeven_rate"]
    assert 0.0 < r < 1.0
    g = grade(ledger, vault, stepup, deployment=Deployment.queue(r))
    assert g.net_contribution_inr == pytest.approx(0.0, abs=1.0)


def test_drag_is_fixed_across_rates(ledger, vault, stepup):
    ra = recontact_arithmetic(ledger, vault, stepup)
    assert len({row["drag_inr"] for row in ra["rows"]}) == 1


def test_rationed_reviewer_reaches_only_the_top_slice(ledger, vault):
    n = len(ledger)
    for cov in HUMAN_COVERAGE:
        h = baseline_human_rationed(ledger, vault, cov)
        assert h.counts["reviewed"] == round(cov * n)
        assert h.n_released <= h.counts["reviewed"]


def test_unreviewed_cases_recover_nothing(ledger, vault):
    """Below the line the block stands. Nobody undid it."""
    amounts = ledger.amounts()
    cov = HUMAN_COVERAGE[0]
    h = baseline_human_rationed(ledger, vault, cov)
    cutoff = np.sort(amounts)[::-1][h.counts["reviewed"] - 1]
    assert amounts[h.released_mask].min() >= cutoff


def test_more_coverage_never_recovers_less(ledger, vault):
    prev = baseline_human_rationed(ledger, vault, HUMAN_COVERAGE[0])
    for cov in HUMAN_COVERAGE[1:]:
        h = baseline_human_rationed(ledger, vault, cov)
        assert h.recovered_inr >= prev.recovered_inr
        prev = h


def test_rationed_charges_only_for_cases_reviewed(ledger, vault):
    for cov in HUMAN_COVERAGE:
        h = baseline_human_rationed(ledger, vault, cov)
        assert h.review_cost_inr == pytest.approx(REVIEW_COST_INR * h.counts["reviewed"])


def test_full_coverage_rationed_matches_full_reviewer(ledger, vault):
    """The rationed reviewer at 100% coverage is the same person as the
    unrationed one, so the two must agree."""
    full = baseline_human_reviewer(ledger, vault, HUMAN_ACCURACY, seed=11)
    rat = baseline_human_rationed(ledger, vault, 1.0, HUMAN_ACCURACY, seed=11)
    assert rat.n_released == full.n_released
    assert rat.recovered_inr == pytest.approx(full.recovered_inr)
    assert rat.net_contribution_inr == pytest.approx(full.net_contribution_inr)


def test_coverage_beats_accuracy(ledger, vault, stepup, store, model):
    """The claim being made in METRICS.md 2: a reviewer wins case for case and
    loses on reach. If this ever flips, the narrative is wrong."""
    o = grade(ledger, vault, stepup)
    full = baseline_human_reviewer(ledger, vault)
    assert full.net_contribution_inr > o.net_contribution_inr
    for cov in HUMAN_COVERAGE:
        h = baseline_human_rationed(ledger, vault, cov)
        assert h.net_contribution_inr < o.net_contribution_inr
        assert h.recall_recoverable < o.recall_recoverable


# ---------------------------------------------------------------------------
# The demo screen
# ---------------------------------------------------------------------------
def test_case_room_contains_every_showcase_case(store, vault, model, data_dir):
    """Five of the six showcase cases sit past position 400 in chronological
    order. An earlier version took a plain prefix of the holdout, so the demo
    screen rendered one tagged case out of six and none of the recorded model
    verdicts were ever requested. Nothing threw and no test failed; the page
    just quietly had nothing on it worth showing.
    """
    from core.showcase import pick as pick_showcase
    from service.case_room import collect

    want = {e.payment_id for e, _, _ in pick_showcase(store, vault, model).values()}
    got = collect(data_dir, limit=10)
    ids = {c["payment_id"] for c in got}
    assert want <= ids, f"missing from the case room: {sorted(want - ids)}"
    # and they have to be at the top, not buried
    assert all(c["roles"] for c in got[:len(want)])


def test_recorded_model_verdicts_reach_the_demo(needs_model, data_dir):
    """Whatever is in the replay cache should show up as model output on the
    screen. If this drops to zero the demo is all templates again."""
    from service.case_room import collect
    got = collect(data_dir, limit=10)
    assert any(c["verdict_source"] == "cache" for c in got)


def test_every_rendered_case_declares_its_provenance(needs_model, data_dir):
    """Item 3 of the review: a screenshot must not imply model output that is
    not there. Every case on the page carries a provenance string, and the
    model/template split is stated in the header, not buried."""
    import re

    from service.case_room import build
    out = build(out="artifacts/_provenance_check.html", data_dir=data_dir, limit=12)
    html = open(out, encoding="utf-8").read()
    cases = json.loads(re.search(r"const CASES=(\[.*?\]);", html, re.S).group(1))
    assert cases
    for c in cases:
        assert c["provenance"], c["payment_id"]
        if c["from_model"]:
            assert "template" not in c["provenance"].lower()
        else:
            assert "not model output" in c["provenance"].lower()
    n_model = sum(c["from_model"] for c in cases)
    assert f"{n_model} of {len(cases)} verdicts were written by a model" in html
    os.remove(out)


# ---------------------------------------------------------------------------
# The browser console
# ---------------------------------------------------------------------------
def test_exported_model_reproduces_the_fitted_one(needs_model, data_dir, tmp_path):
    """The agent page runs the exported trees. If they drift from the fitted
    model the page is confidently wrong, which is worse than being broken, so
    the exporter refuses to write and this asserts it stays that way."""
    from service.export_bundle import build
    out = build(data_dir=data_dir, out=str(tmp_path / "bundle.json"))
    b = json.loads(open(out, encoding="utf-8").read())
    assert b["model"]["n_trees"] > 0
    assert b["model"]["features"], "no feature order exported"
    assert len(b["samples"]) == 6, "the agent page needs all six demo cases"
    for s in b["samples"]:
        for f in b["model"]["features"]:
            if f == "amount":
                continue
            assert f in s["json"], f"sample {s['json']['payment_id']} is missing {f}"


def test_console_is_self_contained(data_dir, tmp_path):
    """It has to open from disk with the network unplugged, like the case room."""
    import re
    from service.dashboard import build
    # rebuild=False: this checks how the page is assembled, which does not need
    # the model re-exported, and re-exporting drags in a native library that is
    # not always loadable.
    out = build(out=str(tmp_path / "dashboard.html"), data_dir=data_dir,
                rebuild=not os.path.exists(os.path.join("artifacts", "bundle.json")))
    html = open(out, encoding="utf-8").read()
    assert "__BUNDLE__" not in html
    refs = re.findall(r'(?:src|href)="((?:https?:)?//[^"]+)', html)
    assert all("fonts.googleapis.com" in r or "fonts.gstatic.com" in r for r in refs), \
        f"page loads something other than fonts: {refs}"


def test_local_server_serves_every_route(data_dir):
    """`python run.py serve` is how the console is meant to be looked at, so
    the routes it advertises have to actually resolve."""
    import socket
    import threading
    from service import serve as srv

    if not os.path.exists(os.path.join(srv.ARTIFACTS, "dashboard.html")):
        pytest.skip("no built console; run `python run.py console`")

    class S(srv.socketserver.ThreadingMixIn, srv.http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    httpd = S(("127.0.0.1", 0), srv.Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        # one keep-alive connection, the way a browser loads a page
        c = socket.create_connection(("127.0.0.1", port), timeout=10)
        try:
            for path in ("/", "/console", "/case", "/artifacts/headline.json"):
                c.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\n"
                          f"Connection: keep-alive\r\n\r\n".encode())
                buf = b""
                while b"\r\n\r\n" not in buf:
                    buf += c.recv(65536)
                head, _, rest = buf.partition(b"\r\n\r\n")
                assert b"200 OK" in head.split(b"\r\n")[0], f"{path}: {head[:60]}"
                length = int(next(l.split(b":")[1] for l in head.split(b"\r\n")
                                  if l.lower().startswith(b"content-length")))
                while len(rest) < length:
                    rest += c.recv(65536)
                assert len(rest) == length, path
        finally:
            c.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_console_layout_is_responsive():
    """The layout shipped with a hardcoded 61px header height, a two-column
    grid that could not collapse below 630px, and a 960px diagram with no
    scroll container. This audit finds five problems on that version and none
    on this one."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not on PATH")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not os.path.exists(os.path.join(root, "artifacts", "dashboard.html")):
        pytest.skip("no built console; run `python run.py console`")
    # stdin=DEVNULL, not the default: under pytest's capture the inherited
    # stdin has no OS handle, and Popen fails with WinError 6 trying to
    # duplicate one. It passes standalone and fails in the suite without it.
    r = subprocess.run([node, "service/check_responsive.js"], cwd=root,
                       capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout
