"""Build the browser console: one self-contained HTML file, no server.

    python -m service.dashboard

Inlines artifacts/bundle.json into the template, so the page opens from disk
with the network unplugged and the agent page runs the real model rather than a
lookup table. Regenerates the bundle first if it is missing or stale.
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "dashboard_template.html")
OUT = os.path.join("artifacts", "dashboard.html")
BUNDLE = os.path.join("artifacts", "bundle.json")


def build(out: str = OUT, data_dir: str = "data300k", rebuild: bool = True) -> str:
    # Imported here, not at module scope: re-exporting the bundle needs
    # LightGBM, but rewriting the page around an existing bundle does not, and
    # a template change should not be blocked by the ML stack being
    # unavailable.
    if rebuild or not os.path.exists(BUNDLE):
        from . import export_bundle
        export_bundle.build(data_dir=data_dir, out=BUNDLE)

    with open(BUNDLE, encoding="utf-8") as fh:
        bundle = fh.read()
    with open(TEMPLATE, encoding="utf-8") as fh:
        page = fh.read()

    if "__BUNDLE__" not in page:
        raise ValueError("template has no __BUNDLE__ placeholder")
    # The bundle is JSON, so it can only break out of the <script> through a
    # literal </script>. Nothing in it should contain one, but check rather
    # than assume, since payment ids and verdict text pass through here.
    if "</script" in bundle.lower():
        raise ValueError("bundle contains a script close tag")
    page = page.replace("__BUNDLE__", bundle)

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(page)
    return out


if __name__ == "__main__":
    p = build()
    print(f"wrote {p} ({os.path.getsize(p) / 1024:.0f} KB)")
