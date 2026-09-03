"""Build the stage: the version meant to be looked at across a room.

    python -m service.stage

Same bundle and the same service/engine.js as the console on :4000, so the two
front ends cannot disagree about what the model says - there is one copy of the
model and one copy of the recovery ladder, and the checks that verify them
verify them for both.

What differs is the job. The console is read by somebody sitting down; this is
watched by somebody walking past. One screen, four scenes on the number keys,
dark by default, and the money large enough to read from a distance.
"""
from __future__ import annotations

import os

from .dashboard import BUNDLE, inject_engine

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "stage_template.html")
OUT = os.path.join("artifacts", "stage.html")


def build(out: str = OUT, data_dir: str = "data300k", rebuild: bool = False) -> str:
    if rebuild or not os.path.exists(BUNDLE):
        from . import export_bundle
        export_bundle.build(data_dir=data_dir, out=BUNDLE)

    with open(BUNDLE, encoding="utf-8") as fh:
        bundle = fh.read()
    with open(TEMPLATE, encoding="utf-8") as fh:
        page = fh.read()

    if "__BUNDLE__" not in page:
        raise ValueError("stage template has no __BUNDLE__ placeholder")
    if "</script" in bundle.lower():
        raise ValueError("bundle contains a script close tag")
    page = inject_engine(page)
    page = page.replace("__BUNDLE__", bundle)

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(page)
    return out


if __name__ == "__main__":
    p = build()
    print(f"wrote {p} ({os.path.getsize(p) / 1024:.0f} KB)")
