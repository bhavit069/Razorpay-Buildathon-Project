#!/usr/bin/env python3
"""
Task runner. `make` isn't installed on this box, so this is the canonical entry
point; the Makefile just delegates here for parity with the architecture doc.

    python run.py data        # 100k baseline (reproduces the committed stats)
    python run.py data300k    # 300k working set (tighter CIs, Day-1 gate)
    python run.py validate    # is the signal there, and does the network add lift?
    python run.py moat        # the same question, priced in rupees
    python run.py sweep       # intercept sensitivity across block-rate regimes
    python run.py metrics     # regenerate METRICS.md + artifacts/ figures
    python run.py test        # the Phase 1 test suite
    python run.py notebooks   # rebuild and re-execute the explainers
    python run.py seeds       # how much does all of this move between worlds?
    python run.py clean
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
# The pipeline prints rupee symbols; Windows consoles default to cp1252.
ENV = {**os.environ, "PYTHONUTF8": "1"}


def sh(*args):
    print(f"$ {' '.join(str(a) for a in args)}", flush=True)
    r = subprocess.run([PY, *[str(a) for a in args]], cwd=ROOT, env=ENV)
    if r.returncode:
        sys.exit(r.returncode)


def data():
    sh("datagen/generate.py", "--n", 100_000, "--seed", 42, "--out", "./data")


def data300k():
    # --customers scales with --n by default; holding it fixed would triple
    # orders-per-customer and push the block rate from 2.8% to 6.3%.
    sh("datagen/generate.py", "--n", 300_000, "--seed", 42, "--out", "./data300k")


def validate():
    sh("datagen/validate_signal.py", _dataset())


def moat():
    sh("datagen/moat_ledger.py", _dataset())


def sweep():
    for i in ("-3.5", "-4.4", "-4.9", "-5.4"):
        out = f"./data/_sweep/i{i}"
        print(f"\n===== intercept {i} =====", flush=True)
        sh("datagen/generate.py", "--n", 100_000, "--seed", 42,
           "--intercept", i, "--out", out)
        sh("datagen/validate_signal.py", out)


def metrics():
    sh("-m", "core.report")


def test():
    sh("-m", "pytest", "tests", "-q")


def notebooks():
    sh("notebooks/build_notebooks.py")


def seeds():
    sh("seed_check.py")


def clean():
    for d in ("data", "data300k", "artifacts"):
        p = os.path.join(ROOT, d)
        if os.path.isdir(p):
            shutil.rmtree(p)
            print(f"removed {d}/")


def _dataset():
    """Prefer the 300k working set; fall back to the 100k baseline."""
    for d in ("./data300k", "./data"):
        if os.path.isdir(os.path.join(ROOT, d.lstrip("./"))):
            return d
    sys.exit("No dataset found. Run `python run.py data300k` first.")


TASKS = {f.__name__: f for f in (data, data300k, validate, moat, sweep, metrics, test, notebooks, seeds, clean)}

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if name not in TASKS:
        sys.exit(f"usage: python run.py {{{'|'.join(TASKS)}}}")
    TASKS[name]()
