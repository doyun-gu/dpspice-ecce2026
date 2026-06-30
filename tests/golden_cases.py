"""Shared definitions for the golden-reference regression tests.

The capture script (``--update`` in ``test_golden.py``) and the regression
test both import this module, so the *frozen* values and the *live* values are
always produced by exactly the same code. Each entry recomputes one number
from a real engine run; nothing here is hard-coded from the paper. The frozen
copies live in ``golden_reference.json`` alongside their tolerance and a pointer
to the paper artifact they correspond to.

Every value is captured from the DEFAULT adaptive public API (``dpspice.api``),
i.e. exactly what a user gets from ``dpspice.run`` / ``dpspice.validate`` — not
from an internal fixed-step path. The solver is deterministic (bit-identical
run to run), so the only spread a golden must tolerate is cross-platform LAPACK
/ numpy floating-point variation, which the per-entry tolerances cover.
"""
from __future__ import annotations

import os
import time
from typing import Callable, Dict

import numpy as np

from dpspice import api, compare
from dpspice._ngspice import ngspice_available

_HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.normpath(os.path.join(_HERE, "..", "examples"))

# Fixed L, C of the paper's series-RLC benchmark (rlc.sp); resonance ~580 krad/s.
_L, _C = "100.04u", "30.07n"


def _rlc_netlist(omega_rad_s: float) -> str:
    f = omega_rad_s / (2 * np.pi)
    tstop = 12.0 / f
    return (f"* Series RLC benchmark, driven at {omega_rad_s:g} rad/s\n"
            f"V1 in 0 SINE(0 1 {f:.6g})\n"
            f"R1 in n2 3.0\n"
            f"L1 n2 out {_L}\n"
            f"C1 out 0 {_C}\n"
            f"R2 out 0 2k\n"
            f".tran 0 {tstop:.6g}\n.end\n")


def _rectifier_netlist(cap_f: float) -> str:
    smoothing = f"C1 out 0 {cap_f:g}\n" if cap_f > 0 else ""
    return (f"* Half-wave rectifier, C={cap_f:g} F\n"
            f"V1 in 0 SINE(0 5 50)\n"
            f"D1 in out Dmod\n"
            f"R1 out 0 1k\n"
            f"{smoothing}"
            f".model Dmod D(Is=1e-9 N=1)\n"
            f".tran 0 1.0 0.96 1u\n.end\n")


_COUPLED_K09 = ("* Coupled inductors k=0.9\n"
                "V1 in 0 SINE(0 1 1000)\n"
                "Rs in p 10\nL1 p 0 1m\nL2 out 0 1m\nRload out 0 1k\n"
                "K1 L1 L2 0.9\n.tran 0 0.02\n.end\n")

_WPT_K02 = ("* Series-series WPT link k=0.2\n"
            "V1 in 0 SINE(0 10 50329.2)\n"
            "Rs in a 1\nC1 a b 1e-07\nL1 b 0 1e-4\nL2 c 0 1e-4\nC2 c out 1e-07\n"
            "Rload out 0 10\nK1 L1 L2 0.2\n.tran 0 0.000794767\n.end\n")


def _waveform(result, node: str = "out"):
    want = f"v({node})"
    for w in result.waveforms:
        if w.name.lower() == want:
            return np.asarray(w.t, dtype=float), np.asarray(w.v, dtype=float)
    raise KeyError(f"node {node} not in waveforms")


# ----------------------------------------------------------------------
# Individual metric computations (each returns one float)
# ----------------------------------------------------------------------

def _idp_vs_td(omega: float, metric: str) -> float:
    nl = _rlc_netlist(omega)
    ri = api.load(nl).run(mode="idp")
    rt = api.load(nl).run(mode="td")
    ti, vi = _waveform(ri)
    tt, vt = _waveform(rt)
    m = compare.compare_on_time_grid(tt, vt, ti, vi,
                                     grid_points=min(len(ti), len(tt)))
    return float(m[metric])


def _conduction_angle(cap_f: float) -> float:
    r = api.load(_rectifier_netlist(cap_f)).run()
    return float(r.summary.get("conduction_angle_deg"))


def _rectifier_nrmse(harmonics) -> float:
    raw = os.path.join(EXAMPLES, "rectifier_halfwave.raw")
    nl = os.path.join(EXAMPLES, "rectifier_halfwave.sp")
    v = api.load(nl).validate(ref=raw, harmonics=harmonics)
    return float(v.worst_nrmse)


def _ngspice_nrmse(netlist: str) -> float:
    return float(api.load(netlist).validate(ref=None).worst_nrmse)


def _sweep_nrmse(periods: int) -> float:
    """IDP-vs-TD NRMSE at a given simulated horizon (reproduce duration sweep).
    Demonstrates that accuracy degrades as the window lengthens."""
    from dpspice import reproduce
    return float(reproduce.duration_sweep([periods])[0]["nrmse"])


def speedup_trend():
    """Return (speedup@50p, speedup@200p) for the RLC IDP-vs-TD sweep. Timings
    are machine-dependent; only the growth ratio is asserted by the test."""
    from dpspice import reproduce
    rows = {r["periods"]: r["speedup"] for r in reproduce.duration_sweep([50, 200])}
    return rows[50], rows[200]


def _idp_scaling_ratio() -> float:
    """solve-time(10x duration) / solve-time(1x). Sublinear (<<10) is the
    mechanism behind the paper's tenfold-per-decade speedup."""
    def solve_s(mult: int) -> float:
        f = 580e3 / (2 * np.pi)
        nl = (f"* RLC\nV1 in 0 SINE(0 1 {f:.6g})\nR1 in n2 3.0\n"
              f"L1 n2 out {_L}\nC1 out 0 {_C}\nR2 out 0 2k\n"
              f".tran 0 {mult * 40.0 / f:.6g}\n.end\n")
        t0 = time.perf_counter()
        api.load(nl).run(mode="idp", with_waveforms=False)
        return time.perf_counter() - t0
    # Average a couple of reps to damp timer noise; ratio is what we assert.
    t1 = min(solve_s(1) for _ in range(2))
    t10 = min(solve_s(10) for _ in range(2))
    return t10 / max(t1, 1e-9)


# name -> (callable, requires_ngspice)
CASES: Dict[str, tuple] = {
    "rlc_idp_vs_td_580krad_nrmse": (lambda: _idp_vs_td(580e3, "nrmse"), False),
    "rlc_idp_vs_td_580krad_r2":    (lambda: _idp_vs_td(580e3, "r2"), False),
    "rlc_idp_vs_td_650krad_nrmse": (lambda: _idp_vs_td(650e3, "nrmse"), False),
    "rlc_idp_vs_td_650krad_r2":    (lambda: _idp_vs_td(650e3, "r2"), False),
    "rlc_idp_vs_td_sweep_50p_nrmse":  (lambda: _sweep_nrmse(50), False),
    "rlc_idp_vs_td_sweep_200p_nrmse": (lambda: _sweep_nrmse(200), False),
    "rectifier_conduction_halfwave_deg": (lambda: _conduction_angle(0.0), False),
    "rectifier_conduction_C10u_deg":     (lambda: _conduction_angle(10e-6), False),
    "rectifier_conduction_C100u_deg":    (lambda: _conduction_angle(100e-6), False),
    "rectifier_nrmse_vs_ltspice_autoK":  (lambda: _rectifier_nrmse(None), False),
    "rectifier_nrmse_vs_ltspice_K40":    (lambda: _rectifier_nrmse(40), False),
    "rlc_nrmse_vs_ngspice":      (lambda: _ngspice_nrmse(_rlc_netlist(580e3)), True),
    "coupled_k0.9_nrmse_vs_ngspice": (lambda: _ngspice_nrmse(_COUPLED_K09), True),
    "wpt_k0.2_nrmse_vs_ngspice":     (lambda: _ngspice_nrmse(_WPT_K02), True),
}


def compute(name: str) -> float:
    fn, _ = CASES[name]
    return fn()


def has_ngspice() -> bool:
    return ngspice_available()
