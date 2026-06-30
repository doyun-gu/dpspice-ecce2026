"""Section 1 - golden-reference regression tests.

Each headline number the paper reports is frozen once in
``golden_reference.json`` and re-checked here against a fresh engine run. The
fixture stores the captured value together with its tolerance and a pointer to
the paper artifact it backs. A test fails when the live number drifts outside
``atol + rtol*|value|`` of the frozen one, which is how a refactor or a future
C backend that silently shifts a published result gets caught.

Re-freeze (only when you *intend* to change a number) with:

    pytest tests/test_golden.py --update-golden

Machine-dependent timings are NOT frozen as wall-clock; the scaling test
asserts the sublinear-in-duration *trend* instead (see test_idp_scaling).
"""
from __future__ import annotations

import json
import os

import pytest

import golden_cases as gc

_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "golden_reference.json")


def _load():
    with open(_FIXTURE) as fh:
        return json.load(fh)


def _all_names():
    return list(_load()["entries"].keys())


@pytest.mark.parametrize("name", _all_names())
def test_golden(name, request):
    entry = _load()["entries"][name]
    if entry.get("requires_ngspice") and not gc.has_ngspice():
        pytest.skip("ngspice not installed; cross-validation golden skipped")

    live = gc.compute(name)

    if request.config.getoption("--update-golden"):
        _rewrite(name, live)
        pytest.skip(f"updated golden {name} -> {live!r}")

    frozen = entry["value"]
    tol = entry["atol"] + entry["rtol"] * abs(frozen)
    assert abs(live - frozen) <= tol, (
        f"{name}: live={live!r} drifted from frozen={frozen!r} "
        f"by {abs(live - frozen):.3e} > tol {tol:.3e}. "
        f"If this change is intended, re-freeze with --update-golden."
    )


def test_idp_scaling():
    """Timings are machine-dependent, so freeze the *trend* not the ms:
    extending the simulated window 10x must cost far less than 10x solve time
    (the sublinear cost is exactly the IDP speedup mechanism)."""
    ratio = gc._idp_scaling_ratio()
    assert ratio < 5.0, (
        f"10x-duration / 1x-duration solve-time ratio = {ratio:.2f}; "
        f"expected sublinear (<<10). A near-linear ratio means the single-shift "
        f"advantage regressed."
    )


def test_speedup_trend():
    """The IDP-vs-TD speedup must grow with the simulated horizon (~tenfold per
    decade of duration). IDP cost is flat (constant envelope) while TD cost
    scales with carrier cycles, so a 4x longer window must yield a clearly
    larger speedup. Absolute milliseconds are machine-dependent and not frozen;
    only the growth ratio is asserted."""
    s50, s200 = gc.speedup_trend()
    assert s200 > s50, f"speedup did not grow with duration: 50p={s50:.2f} 200p={s200:.2f}"
    ratio = s200 / s50
    assert ratio >= 2.5, (
        f"speedup grew only {ratio:.2f}x for a 4x longer window; expected near-"
        f"linear-in-duration growth (tenfold per decade). The IDP advantage may "
        f"have regressed."
    )


def _rewrite(name, live):
    data = _load()
    data["entries"][name]["value"] = live
    with open(_FIXTURE, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
