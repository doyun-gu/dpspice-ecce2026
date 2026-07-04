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

The warm start is a best-effort accelerator, not a guarantee: it cuts the
iteration count sharply where it lands in the basin (e.g. the hybrid_mix
example, 58 -> 22 iterations) but it can miss on stiff switched-diode nodes
where the cold continuation would still converge (the snubbered asynchronous
boost is one). ``solve_hybrid`` therefore falls back to the cold path
automatically whenever the warm-started Newton fails to converge, so enabling
``use_warm_start`` never returns a worse answer than the default.

When the plain (cold) path itself fails — the K-fragile stalls recorded in
the validation report §7 — ``solve_hybrid`` engages continuation: geometric
source stepping from 10% amplitude with warm starts and adaptive step-back,
then SPICE-style Gmin stepping at full source if the ramp is insufficient.
A case plain Newton already solves never enters this code, so converged
results are byte-identical to the plain path.
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from .. import _engine  # noqa: F401  side-effect: flat engine modules on sys.path

import aft                                # noqa: E402
import hb_solver as hb                    # noqa: E402
from mna import stack_to_nodes, nodes_to_stack   # noqa: E402

from .ltp import SwitchedHBNet, source_stack

#: Default clamp on the warm-start peak diode junction voltage, volts.
VD_CLAMP_DEFAULT = 0.4

#: Geometric source-stepping ratio: 0.1 -> 0.178 -> 0.316 -> 0.562 -> 1.0.
_SRC_RATIO = 10.0 ** 0.25
#: Gmin ladder: start conductance and geometric descent factor.
_GMIN_START = 1e-2
_GMIN_FLOOR = 1e-9
_GMIN_FACTOR = 10.0


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


def _newton_fixed(swnet, diodes, Yb, B, K, X, tol, max_iter):
    """Damped Newton at a FIXED operator and source. (X, res, iters, ok).

    Same kernel, damping and 100x residual leniency as the engine's
    ``solve_newton``; factored here so the continuation drivers can run it
    at scaled sources and Gmin-augmented operators without touching the
    engine.
    """
    n = swnet.n
    N = aft.oversample_N(K)
    res = np.inf
    for it in range(max_iter):
        vt, _ = hb._nodes_time(X, K, n, N)
        f_t, glist = hb._diode_current_time(diodes, vt)
        Fnl = nodes_to_stack(aft.to_freq(f_t, K, N), K, n)
        F = Yb @ X + B + Fnl
        res = float(np.max(np.abs(F)))
        if res < tol:
            return X, res, it, True
        J = Yb + hb._diode_jacobian(glist, K, n, N)
        dX = np.linalg.solve(J, -F)
        X = hb._damped_update(X, dX, Yb, B, swnet, diodes, K, n, N)
    vt, _ = hb._nodes_time(X, K, n, N)
    f_t, _ = hb._diode_current_time(diodes, vt)
    Fnl = nodes_to_stack(aft.to_freq(f_t, K, N), K, n)
    res = float(np.max(np.abs(Yb @ X + B + Fnl)))
    return X, res, max_iter, res < tol * 100


def _source_stepping(swnet, diodes, f_sw, K, tol, max_iter):
    """Geometric source ramp 10% -> 100% with warm starts and adaptive
    step-back. Returns (X, res, alphas, iters); X is None on failure."""
    w0 = 2 * np.pi * f_sw
    Yb, _ = swnet.assemble_linear(w0, K)
    Bfull = source_stack(swnet, w0, K)
    alphas, total = [], 0

    # first rung: from the zero iterate (diodes off); back off if even the
    # 10% source defeats plain Newton
    alpha = 0.1
    X = np.zeros_like(Bfull)
    while True:
        Xt, res, it, ok = _newton_fixed(swnet, diodes, Yb, alpha * Bfull, K,
                                        X.copy(), tol, max_iter)
        total += it
        if ok:
            X = Xt
            alphas.append(alpha)
            break
        alpha *= 0.5
        if alpha < 1e-3:
            return None, res, alphas, total

    ratio = _SRC_RATIO
    while alpha < 1.0 - 1e-12:
        target = min(1.0, alpha * ratio)
        Xt, res, it, ok = _newton_fixed(swnet, diodes, Yb, target * Bfull, K,
                                        X.copy(), tol, max_iter)
        total += it
        if ok:
            X, alpha = Xt, target
            alphas.append(target)
            ratio = _SRC_RATIO                 # restore the full step
        else:
            ratio = np.sqrt(ratio)             # halve the geometric step
            if ratio < 1.01:
                return None, res, alphas, total
    return X, res, alphas, total


def _gmin_stepping(swnet, diodes, f_sw, K, tol, max_iter, X0=None):
    """SPICE-style Gmin stepping at full source: a shunt gmin from every
    node-voltage row to ground, relaxed geometrically to zero with warm
    starts and adaptive step-back. Returns (X, res, gmins, iters)."""
    w0 = 2 * np.pi * f_sw
    Yb, _ = swnet.assemble_linear(w0, K)
    Bfull = source_stack(swnet, w0, K)
    n = swnet.n
    H = 2 * K + 1
    n_nodes = swnet.mna.n_nodes                # gmin on voltage rows only
    diag = np.array([a * n + p for a in range(H) for p in range(n_nodes)])

    def solve_at(g, X):
        Yg = Yb.copy()
        Yg[diag, diag] += g
        return _newton_fixed(swnet, diodes, Yg, Bfull, K, X, tol, max_iter)

    gmins, total = [], 0
    g = _GMIN_START
    X = np.zeros_like(Bfull) if X0 is None else X0.copy()
    Xg, res, it, ok = solve_at(g, X.copy())
    total += it
    if not ok:
        return None, res, gmins, total
    X = Xg
    gmins.append(g)

    factor = _GMIN_FACTOR
    while g > 0.0:
        target = 0.0 if g / factor < _GMIN_FLOOR else g / factor
        Xt, res, it, ok = solve_at(target, X.copy())
        total += it
        if ok:
            X, g = Xt, target
            gmins.append(target)
            factor = _GMIN_FACTOR
        else:
            factor = np.sqrt(factor)           # halve the geometric step
            if factor < 1.05:
                return None, res, gmins, total
    return X, res, gmins, total


def solve_hybrid(swnet: SwitchedHBNet, diodes: List, f_sw: float, K: int,
                 tol: float = 1e-10, max_iter: int = 60,
                 use_warm_start: bool = False,
                 vd_clamp: float = VD_CLAMP_DEFAULT,
                 continuation: bool = True):
    """Newton HB on the switch-folded operator. Returns an HBResult.

    Plain Newton (the engine path, with its built-in linear ramp and
    K-continuation) runs first and its converged result is returned
    unchanged — the continuation below engages ONLY on failure, so
    already-converging cases are byte-identical to the plain path. On
    failure: geometric source stepping (10% -> 100%, warm-started, adaptive
    step-back), then Gmin stepping at full source if that was insufficient.
    The returned result carries a ``continuation`` dict recording what ran.
    """
    if not swnet.sampled_gates:
        raise ValueError("solve_hybrid needs a SwitchedHBNet built with "
                         "sampled_gates=True (AFT-consistent switch blocks)")
    if use_warm_start:
        X0 = warm_start(swnet, diodes, f_sw, K, vd_clamp=vd_clamp)
        result = hb.solve_newton(swnet, diodes, f_sw, K=K, tol=tol,
                                 max_iter=max_iter, X0=X0)
        if result.converged:
            return result
        # The warm start missed the basin (a known failure mode on stiff
        # switched-diode nodes). Fall back to the cold path so the option
        # is never worse than the default.
    result = hb.solve_newton(swnet, diodes, f_sw, K=K, tol=tol,
                             max_iter=max_iter, X0=None)
    if result.converged or not continuation:
        return result

    plain_iters = result.iters
    X, res, alphas, iters = _source_stepping(swnet, diodes, f_sw, K,
                                             tol, max_iter)
    meta = {"engaged": True, "source_steps": alphas, "gmin_steps": [],
            "newton_iters": iters}
    if X is None:
        X, res, gmins, git = _gmin_stepping(swnet, diodes, f_sw, K,
                                            tol, max_iter)
        meta["gmin_steps"] = gmins
        meta["newton_iters"] += git
    if X is None:
        result.continuation = meta             # both ladders exhausted
        return result                          # the plain failure, loud
    w0 = 2 * np.pi * f_sw
    out = hb.HBResult(stack_to_nodes(X, K, swnet.n), K, w0, swnet,
                      iters=plain_iters + meta["newton_iters"],
                      residual=res, converged=True,
                      route="newton+continuation")
    out.continuation = meta
    return out
