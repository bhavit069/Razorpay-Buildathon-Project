"""Write RECOVERY.md.

    python run.py recovery

Fits the model, replays the shipped decision over the holdout, runs the
outreach ladder across it and writes the document. Separate from METRICS.md on
purpose: that file is frozen and measured, this one rests on declared rates.
"""
from __future__ import annotations

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.backtest import run as backtest_run
from core.feature_store import FeatureStore
from core.model import Adjudicator
from core.policy import PolicyConfig
from core.recovery_doc import to_markdown
from core.truth import TruthVault

OUT = os.path.join("docs", "RECOVERY.md")


def main(data_dir: str = "data300k", cap: float = 0.20) -> str:
    store = FeatureStore.load(data_dir)
    vault = TruthVault(data_dir)
    model = Adjudicator().fit(store, vault)
    ledger = backtest_run(store, model, PolicyConfig(cap=cap), split="holdout")
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(to_markdown(ledger))
    return OUT


if __name__ == "__main__":
    print(f"wrote {main()}")
