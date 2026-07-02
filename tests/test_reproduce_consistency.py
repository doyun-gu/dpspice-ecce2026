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


def test_reproduce_table5_matches_golden():
    """`reproduce --table 5` must emit all three Table V rows (resistive, RC
    mild, RC strong), and each row's NRMSE-vs-LTspice must match its frozen
    golden within tolerance. Guards against the target silently reverting to a
    single wrong load (the Task 1 bug)."""
    rows = {r["case"]: r for r in reproduce.reproduce(table=5)["rows"]}
    assert set(rows) == {"resistive", "RC, mild", "RC, strong"}, (
        f"table 5 emitted rows {sorted(rows)}; expected the three Table V loads."
    )
    for case, name in (
        ("resistive", "rectifier_table5_resistive_nrmse_K15"),
        ("RC, mild", "rectifier_table5_rcmild_nrmse_K30"),
        ("RC, strong", "rectifier_table5_rcstrong_nrmse_K40"),
    ):
        g = _golden(name)
        live = float(rows[case]["nrmse_vs_ltspice"])
        tol = g["atol"] + g["rtol"] * abs(g["value"])
        assert abs(live - g["value"]) <= tol, (
            f"reproduce --table 5 [{case}] nrmse={live!r} disagrees with golden "
            f"{g['value']!r} (tol {tol:.3e}). If intended, re-freeze the golden; "
            f"if this contradicts the paper, record it in PAPER_CODE_MISMATCHES.md."
        )


def test_reproduce_table3_emits_per_duration_accuracy():
    """`reproduce --table 3` must carry per-duration IDP-vs-TD NRMSE / R^2 /
    speedup (the paper's Table III + Table II accuracy/speedup content), not just
    timings."""
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
