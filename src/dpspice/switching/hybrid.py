"""Hybrid NR-HB: gated switches in the linear operator, diodes in Newton.

In a mixed netlist (gated switches + diodes) the diode's Toeplitz block
changes every Newton iteration -- its g(t) depends on the iterate -- but the
routed switch's g_sw(t) is known before the solve. The switch blocks are
therefore assembled ONCE into the linear operator (SwitchedHBNet with
sampled gates, so the constant block lives on the same AFT collocation grid
as the diode residual) and the unmodified Newton solver only ever refreshes
the diode blocks.

Optional clamped warm start: an LTP-only pre-solve with the diode linearised
at its DC conductance, scaled so the peak diode junction voltage equals
``vd_clamp`` (default 0.4 V). The raw LTP pre-solve overshoots the junction
voltage by volts and the exponential diode diverges from there; clamping the
excursion keeps the first Newton step inside the basin. The warm iterate
replaces both continuation safeguards (source stepping, K-continuation).
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from .. import _engine  # noqa: F401  side-effect: flat engine modules on sys.path

import aft                                # noqa: E402
import hb_solver as hb                    # noqa: E402
from mna import stack_to_nodes            # noqa: E402

from .ltp import SwitchedHBNet, source_stack

#: Default clamp on the warm-start peak diode junction voltage, volts.
VD_CLAMP_DEFAULT = 0.4


def warm_start(swnet: SwitchedHBNet, diodes: List, f_sw: float, K: int,
               vd_clamp: float = VD_CLAMP_DEFAULT) -> np.ndarray:
    """LTP pre-solve with diodes linearised at DC, clamped to vd_clamp."""
    w0 = 2 * np.pi * f_sw
    n = swnet.n
    Yb, _ = swnet.assemble_linear(w0, K)
    Bfull = source_stack(swnet, w0, K)
    H = 2 * K + 1
    Jd0 = np.zeros_like(Yb)
    for d in diodes:
        g_dc = float(d.law.dIdV(0.0))
        Jd0 += np.kron(np.eye(H, dtype=complex) * g_dc, d.P.astype(complex))
    X = np.linalg.solve(Yb + Jd0, -Bfull)

    # clamp the peak junction excursion across all diodes
    N = aft.oversample_N(K)
    vt = aft.to_time(stack_to_nodes(X, K, n), K, N)
    vd_max = 0.0
    for d in diodes:
        va = vt[d.ia] if d.ia >= 0 else np.zeros(N)
        vb = vt[d.ib] if d.ib >= 0 else np.zeros(N)
        vd_max = max(vd_max, float(np.max(va - vb)))
    if vd_max > vd_clamp:
        X = X * (vd_clamp / vd_max)
    return X


def solve_hybrid(swnet: SwitchedHBNet, diodes: List, f_sw: float, K: int,
                 tol: float = 1e-10, max_iter: int = 60,
                 use_warm_start: bool = False,
                 vd_clamp: float = VD_CLAMP_DEFAULT):
    """Newton HB on the switch-folded operator. Returns an HBResult."""
    if not swnet.sampled_gates:
        raise ValueError("solve_hybrid needs a SwitchedHBNet built with "
                         "sampled_gates=True (AFT-consistent switch blocks)")
    X0: Optional[np.ndarray] = None
    if use_warm_start:
        X0 = warm_start(swnet, diodes, f_sw, K, vd_clamp=vd_clamp)
    return hb.solve_newton(swnet, diodes, f_sw, K=K, tol=tol,
                           max_iter=max_iter, X0=X0)
