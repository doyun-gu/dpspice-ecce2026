"""Structured circuit-family generators with pre-assigned oracles.

Each family emits valid netlists by sweeping parameters, and each generated
:class:`Case` carries its own oracle (closed-form analytic, or ngspice) and a
family-specific NRMSE tolerance band. We never generate random netlists: a
"failure" must mean a solver bug, not a meaningless circuit.

Adding a new family is one new entry in :data:`FAMILIES` — a generator plus a
band plus (optionally) a closed-form function — not a rewrite.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np

from . import oracles


@dataclass
class Case:
    """One generated circuit paired with its ground-truth oracle."""
    family: str
    name: str
    netlist: str
    output_node: str
    oracle: str                      # "closed_form" | "ngspice"
    band: float                      # family NRMSE tolerance (fraction)
    params: Dict = field(default_factory=dict)
    # closed-form evaluator: (t_array) -> v_array, set when oracle == "closed_form"
    analytic: Optional[Callable[[np.ndarray], np.ndarray]] = None


@dataclass
class Family:
    key: str
    description: str
    band: float
    generate: Callable[[bool], List[Case]]   # (quick) -> cases


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

def _eng(x: float) -> str:
    """Format a value in plain SPICE-friendly notation."""
    return f"{x:.6g}"


# ----------------------------------------------------------------------
# RC first-order (closed-form)
# ----------------------------------------------------------------------

def gen_rc(quick: bool) -> List[Case]:
    band = 0.005
    Vm, f = 5.0, 50.0
    combos = [(1e3, 1e-6), (1e3, 1e-5)] if quick else \
             [(1e3, 1e-6), (1e3, 1e-5), (2.2e3, 4.7e-6), (470.0, 1e-7)]
    cases = []
    for R, C in combos:
        tau = R * C
        tstop = max(8.0 / f, 6.0 * tau)
        netlist = (
            f"* RC low-pass R={_eng(R)} C={_eng(C)}\n"
            f"V1 in 0 SINE(0 {_eng(Vm)} {_eng(f)})\n"
            f"R1 in out {_eng(R)}\n"
            f"C1 out 0 {_eng(C)}\n"
            f".tran 0 {_eng(tstop)}\n.end\n"
        )
        cases.append(Case(
            family="rc", name=f"rc_R{_eng(R)}_C{_eng(C)}", netlist=netlist,
            output_node="out", oracle="closed_form", band=band,
            params={"R": R, "C": C, "f": f, "Vm": Vm},
            analytic=lambda t, R=R, C=C: oracles.rc_lowpass(t, Vm, f, R, C),
        ))
    return cases


# ----------------------------------------------------------------------
# RL first-order (closed-form)
# ----------------------------------------------------------------------

def gen_rl(quick: bool) -> List[Case]:
    band = 0.005
    Vm, f = 5.0, 50.0
    combos = [(100.0, 0.1), (1e3, 0.5)] if quick else \
             [(100.0, 0.1), (1e3, 0.5), (47.0, 0.01), (220.0, 1.0)]
    cases = []
    for R, L in combos:
        tau = L / R
        tstop = max(8.0 / f, 6.0 * tau)
        netlist = (
            f"* RL R={_eng(R)} L={_eng(L)}\n"
            f"V1 in 0 SINE(0 {_eng(Vm)} {_eng(f)})\n"
            f"R1 in out {_eng(R)}\n"
            f"L1 out 0 {_eng(L)}\n"
            f".tran 0 {_eng(tstop)}\n.end\n"
        )
        cases.append(Case(
            family="rl", name=f"rl_R{_eng(R)}_L{_eng(L)}", netlist=netlist,
            output_node="out", oracle="closed_form", band=band,
            params={"R": R, "L": L, "f": f, "Vm": Vm},
            analytic=lambda t, R=R, L=L: oracles.rl_output(t, Vm, f, R, L),
        ))
    return cases


# ----------------------------------------------------------------------
# Series RLC (closed-form; the paper's main benchmark) — sweep Q
# ----------------------------------------------------------------------

def gen_series_rlc(quick: bool) -> List[Case]:
    band = 0.005
    Vm = 1.0
    # Fix L, C (resonance ~92 kHz like the paper), drive at resonance, vary R -> Q.
    L, C = 100.04e-6, 30.07e-9
    wn = 1.0 / np.sqrt(L * C)
    fn = wn / (2 * np.pi)
    Qs = [3.0, 19.0] if quick else [1.0, 3.0, 10.0, 19.0]
    cases = []
    for Q in Qs:
        # Q = (1/R) sqrt(L/C)  ->  R = sqrt(L/C)/Q
        R = np.sqrt(L / C) / Q
        zeta = 1.0 / (2 * Q)
        t_settle = 1.0 / (zeta * wn)               # ~1 time-constant of the envelope
        tstop = max(12.0 / fn, 6.0 * t_settle)
        netlist = (
            f"* Series RLC  Q={Q:g}  R={_eng(R)}  L={_eng(L)}  C={_eng(C)}\n"
            f"V1 in 0 SINE(0 {_eng(Vm)} {_eng(fn)})\n"
            f"R1 in n1 {_eng(R)}\n"
            f"L1 n1 out {_eng(L)}\n"
            f"C1 out 0 {_eng(C)}\n"
            f".tran 0 {_eng(tstop)}\n.end\n"
        )
        cases.append(Case(
            family="rlc", name=f"rlc_Q{Q:g}", netlist=netlist,
            output_node="out", oracle="closed_form", band=band,
            params={"R": R, "L": L, "C": C, "f": fn, "Q": Q, "Vm": Vm},
            analytic=lambda t, R=R: oracles.series_rlc_vc(t, Vm, fn, R, L, C),
        ))
    return cases


# ----------------------------------------------------------------------
# RLC ladder (ngspice oracle) — tests MNA scaling on linear topologies
# ----------------------------------------------------------------------

def gen_ladder(quick: bool) -> List[Case]:
    band = 0.01
    Vm, f = 1.0, 1e3
    stages_list = [3, 5] if quick else [2, 3, 5, 8]
    R, L, C = 100.0, 1e-3, 1e-6
    cases = []
    for n in stages_list:
        lines = [f"* RLC ladder, {n} stages",
                 f"V1 in 0 SINE(0 {_eng(Vm)} {_eng(f)})"]
        prev = "in"
        for k in range(1, n + 1):
            node = "out" if k == n else f"n{k}"
            lines.append(f"R{k} {prev} a{k} {_eng(R)}")
            lines.append(f"L{k} a{k} {node} {_eng(L)}")
            lines.append(f"C{k} {node} 0 {_eng(C)}")
            prev = node
        tstop = 20.0 / f
        lines.append(f".tran 0 {_eng(tstop)}")
        lines.append(".end")
        netlist = "\n".join(lines) + "\n"
        cases.append(Case(
            family="ladder", name=f"ladder_{n}stage", netlist=netlist,
            output_node="out", oracle="ngspice", band=band,
            params={"stages": n},
        ))
    return cases


# ----------------------------------------------------------------------
# Coupled inductors / transformer (ngspice oracle) — tests M = k sqrt(L1 L2)
# ----------------------------------------------------------------------

def gen_coupled(quick: bool) -> List[Case]:
    band = 0.01
    Vm, f = 1.0, 1e3
    ks = [0.5, 0.9] if quick else [0.1, 0.5, 0.9, 0.95]
    L1, L2, Rs, Rload = 1e-3, 1e-3, 10.0, 1e3
    cases = []
    for k in ks:
        netlist = (
            f"* Coupled inductors k={k:g}\n"
            f"V1 in 0 SINE(0 {_eng(Vm)} {_eng(f)})\n"
            f"Rs in p {_eng(Rs)}\n"
            f"L1 p 0 {_eng(L1)}\n"
            f"L2 out 0 {_eng(L2)}\n"
            f"Rload out 0 {_eng(Rload)}\n"
            f"K1 L1 L2 {k:g}\n"
            f".tran 0 {_eng(20.0 / f)}\n.end\n"
        )
        cases.append(Case(
            family="coupled", name=f"coupled_k{k:g}", netlist=netlist,
            output_node="out", oracle="ngspice", band=band,
            params={"k": k},
        ))
    return cases


# ----------------------------------------------------------------------
# Series-series WPT link (ngspice oracle) — the paper's k=0.2 case
# ----------------------------------------------------------------------

def gen_wpt(quick: bool) -> List[Case]:
    # Series-compensated coupled resonator. The series tuning caps are floating
    # (between two ungrounded nodes), so the simple subset reduction's E_dd comes
    # out singular; the engine falls back to the general SVD-based index-1
    # reduction (see mna_circuit._setup_general_reduction), which deflates the
    # dangling K-coupled V(L) auxiliary states and solves the topology cleanly.
    band = 0.03   # loose-coupling resonant link; comfortably met in practice
    Vm = 10.0
    # Resonant series-series link tuned to f0; loose coupling.
    L1 = L2 = 100e-6
    f0 = 1.0 / (2 * np.pi * np.sqrt(L1 * 1e-7))   # tune C to resonate L at f0
    C1 = C2 = 1e-7
    ks = [0.2] if quick else [0.2, 0.3]
    Rs, Rload = 1.0, 10.0
    cases = []
    for k in ks:
        netlist = (
            f"* Series-series WPT link k={k:g}\n"
            f"V1 in 0 SINE(0 {_eng(Vm)} {_eng(f0)})\n"
            f"Rs in a {_eng(Rs)}\n"
            f"C1 a b {_eng(C1)}\n"
            f"L1 b 0 {_eng(L1)}\n"
            f"L2 c 0 {_eng(L2)}\n"
            f"C2 c out {_eng(C2)}\n"
            f"Rload out 0 {_eng(Rload)}\n"
            f"K1 L1 L2 {k:g}\n"
            f".tran 0 {_eng(40.0 / f0)}\n.end\n"
        )
        cases.append(Case(
            family="wpt", name=f"wpt_k{k:g}", netlist=netlist,
            output_node="out", oracle="ngspice", band=band,
            params={"k": k, "f0": f0},
        ))
    return cases


# ----------------------------------------------------------------------
# Diode rectifier (ngspice oracle; exercises the HB / K>1 path)
# ----------------------------------------------------------------------

def gen_rectifier(quick: bool) -> List[Case]:
    band = 0.01
    Vm, f = 5.0, 50.0
    # Vary the smoothing cap -> conduction angle changes (half-wave -> DCM).
    Cs = [0.0, 100e-6] if quick else [0.0, 10e-6, 47e-6, 100e-6]
    cases = []
    for C in Cs:
        smoothing = f"C1 out 0 {_eng(C)}\n" if C > 0 else ""
        tag = "halfwave" if C == 0 else f"C{_eng(C)}"
        # Settle several cycles, save the last few; matches the paper's decks.
        netlist = (
            f"* Diode rectifier {tag}\n"
            f"V1 in 0 SINE(0 {_eng(Vm)} {_eng(f)})\n"
            f"D1 in out Dmod\n"
            f"R1 out 0 1k\n"
            f"{smoothing}"
            f".model Dmod D(Is=1e-9 N=1)\n"
            f".tran 0 1.0 0.96 1u\n.end\n"
        )
        cases.append(Case(
            family="rectifier", name=f"rectifier_{tag}", netlist=netlist,
            output_node="out", oracle="ngspice", band=band,
            params={"C": C},
        ))
    return cases


# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------

FAMILIES: Dict[str, Family] = {
    "rc":        Family("rc", "RC first-order (closed-form)", 0.005, gen_rc),
    "rl":        Family("rl", "RL first-order (closed-form)", 0.005, gen_rl),
    "rlc":       Family("rlc", "Series RLC, Q sweep (closed-form)", 0.005, gen_series_rlc),
    "ladder":    Family("ladder", "RLC ladder, N stages (ngspice)", 0.01, gen_ladder),
    "coupled":   Family("coupled", "Coupled inductors (ngspice)", 0.01, gen_coupled),
    "wpt":       Family("wpt", "Series-series WPT link (ngspice)", 0.03, gen_wpt),
    "rectifier": Family("rectifier", "Diode rectifier, HB path (ngspice)", 0.01, gen_rectifier),
}

# Families known to exercise a topology the released engine cannot yet solve.
# Excluded from the default run (the green suite reflects what genuinely works);
# runnable explicitly with `--family <name>` so the limitation stays tracked.
# Empty for now: the WPT series-compensated resonator that used to live here is
# solved by the general DAE reduction and is part of the default suite.
EXPERIMENTAL: set = set()

#: Default coverage = every registered family except the experimental ones.
DEFAULT_FAMILIES = [k for k in FAMILIES if k not in EXPERIMENTAL]


def all_cases(quick: bool = False, only: Optional[List[str]] = None) -> List[Case]:
    keys = only or DEFAULT_FAMILIES
    cases: List[Case] = []
    for key in keys:
        fam = FAMILIES.get(key)
        if fam is None:
            raise KeyError(f"unknown family '{key}' (have: {', '.join(FAMILIES)})")
        cases.extend(fam.generate(quick))
    return cases
