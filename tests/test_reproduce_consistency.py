"""Section 4 - paper <-> code consistency.

The `dpspice reproduce` command is the user-facing way to regenerate paper
artifacts. This test asserts that what `reproduce` emits is the SAME number the
golden fixture froze for the corresponding paper artifact, so the two cannot
drift apart. Where a reproduced value disagrees with the paper *text*, that gap
is documented in REPRODUCIBILITY.md / PAPER_CODE_MISMATCHES.md and is
deliberately NOT reconciled here.
"""
from __future__ import annotations

import json
import os

from dpspice import reproduce

_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "golden_reference.json")


def _golden(name):
    with open(_FIXTURE) as fh:
        return json.load(fh)["entries"][name]


def test_reproduce_table4_matches_golden():
    """`reproduce --table 4` worst NRMSE must match the frozen rectifier-vs-
    LTspice golden (auto harmonic count) within its tolerance."""
    g = _golden("rectifier_nrmse_vs_ltspice_autoK")
    live = float(reproduce.reproduce(table=4)["worst_nrmse"])
    tol = g["atol"] + g["rtol"] * abs(g["value"])
    assert abs(live - g["value"]) <= tol, (
        f"reproduce --table 4 worst_nrmse={live!r} disagrees with golden "
        f"{g['value']!r} (tol {tol:.3e}). If intended, re-freeze the golden; "
        f"if this contradicts the paper, record it in PAPER_CODE_MISMATCHES.md."
    )


def test_reproduce_table3_emits_per_duration_accuracy():
    """`reproduce --table 3` must carry per-duration IDP-vs-TD NRMSE / R^2 /
    speedup (the paper's Table 3 accuracy+speedup content), not just timings."""
    out = reproduce.reproduce(table=3)
    sweep = out.get("idp_vs_td_duration_sweep")
    assert sweep and len(sweep) >= 2, "table 3 missing the duration sweep"
    for row in sweep:
        for key in ("periods", "nrmse", "r2", "speedup", "idp_ms", "td_ms"):
            assert key in row, f"sweep row missing {key!r}"
    # NRMSE must grow with the window (the documented horizon dependence).
    ordered = sorted(sweep, key=lambda r: r["periods"])
    assert ordered[-1]["nrmse"] > ordered[0]["nrmse"], (
        "expected NRMSE to grow with simulated horizon; see PAPER_CODE_MISMATCHES.md"
    )


def test_reproducibility_doc_exists():
    """The paper-artifact map must ship with the release."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for fn in ("REPRODUCIBILITY.md", "PAPER_CODE_MISMATCHES.md"):
        path = os.path.join(root, fn)
        assert os.path.exists(path) and os.path.getsize(path) > 0, f"missing {fn}"
