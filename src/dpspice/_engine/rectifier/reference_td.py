"""
In-house time-domain reference solver (the autonomous ground truth).

Trapezoidal integration of the engine MNA system with one or more nonlinear
devices, each step closed by Newton-Raphson on the device current.  This is the
reference the harmonic-balance solvers validate against WITHOUT LTspice, so the
build can iterate autonomously (build spec section 4).  LTspice enters only as
an independent cross-check at the M6 checkpoint.

System (engine convention):   E dx/dt = A_lin x + b(t) + f_nl(x)
where A_lin is the LINEAR network (diode excluded) and f_nl(x) injects the
device current:  at anode  -I(v_d),  at cathode  +I(v_d),  v_d = x[a]-x[b].

Trapezoidal residual over a step x0 -> x1:
    R(x1) = E (x1-x0) - 0.5 dt [ (A x1 + b1 + f(x1)) + (A x0 + b0 + f(x0)) ]
    J     = E - 0.5 dt [ A + Jf(x1) ],   Jf(x) = g(v_d) P   (P = branch incidence)

Author: Doyun Gu (University of Manchester) -- ECCE 2026 nonlinear extension.
"""
import numpy as np
from mna import HBNet


class Diode:
    """Binds a device law (device.py) to an anode/cathode node pair and
    provides its MNA incidence P and the nonlinear stamps."""

    def __init__(self, hbnet: HBNet, anode, cathode, law):
        self.law = law
        self.ia = hbnet.idx(anode)
        self.ib = hbnet.idx(cathode)
        n = hbnet.n
        P = np.zeros((n, n))
        for x in (self.ia, self.ib):
            if x >= 0:
                P[x, x] -= 1.0
        if self.ia >= 0 and self.ib >= 0:
            P[self.ia, self.ib] += 1.0
            P[self.ib, self.ia] += 1.0
        self.P = P

    def v_across(self, x):
        va = x[self.ia] if self.ia >= 0 else 0.0
        vb = x[self.ib] if self.ib >= 0 else 0.0
        return (va - vb).real if np.iscomplexobj(x) else (va - vb)

    def f_nl(self, x):
        """Current-injection vector: -I at anode, +I at cathode."""
        v = self.v_across(x)
        i = self.law.I(v)
        f = np.zeros_like(x)
        if self.ia >= 0:
            f[self.ia] -= i
        if self.ib >= 0:
            f[self.ib] += i
        return f

    def jac_nl(self, x):
        """Jacobian of f_nl: g(v_d) * P."""
        return self.law.dIdV(self.v_across(x)) * self.P


def simulate(hbnet: HBNet, diodes, f0, n_cycles=200, steps_per_cycle=400,
             keep_cycles=4, newton_tol=1e-10, newton_max=40, vlim=1.0):
    """Fixed-step trapezoidal transient with per-step Newton on the diodes.

    Returns (t, x) for the LAST keep_cycles only (steady state), with x of
    shape (steps_kept, n).  vlim caps the per-iteration junction-voltage change
    (pnjlim-style limiting) so the exponential law cannot overflow."""
    if isinstance(diodes, Diode):
        diodes = [diodes]
    A = hbnet.A.real.copy()
    E = hbnet.E.real.copy()
    n = hbnet.n
    T = 1.0 / f0
    dt = T / steps_per_cycle
    N = n_cycles * steps_per_cycle
    keep = keep_cycles * steps_per_cycle

    def f_all(x):
        f = np.zeros(n)
        for d in diodes:
            f += d.f_nl(x)
        return f

    def jac_all(x):
        J = np.zeros((n, n))
        for d in diodes:
            J += d.jac_nl(x)
        return J

    x = np.zeros(n)
    bfun = lambda t: hbnet.mna.b_func(t).copy().real
    t = 0.0
    b0 = bfun(t)
    f0v = f_all(x)
    buf = np.zeros((keep, n))
    tb = np.zeros(keep)

    for i in range(N):
        t1 = t + dt
        b1 = bfun(t1)
        # constant part of the trapezoidal residual (depends on x0 only)
        const = E @ x + 0.5 * dt * (A @ x + b0 + f0v + b1)
        xx = x.copy()                         # Newton initial guess
        for _ in range(newton_max):
            fx = f_all(xx)
            R = E @ xx - 0.5 * dt * (A @ xx + fx) - const
            J = E - 0.5 * dt * (A + jac_all(xx))
            dx = np.linalg.solve(J, -R)
            # junction-voltage limiting
            for d in diodes:
                vd_idx_a = d.ia
                vd_idx_b = d.ib
                # cap change in v across the device
            step = dx
            # simple global damping on the largest node-voltage swing
            mx = np.max(np.abs(step[:hbnet.mna.n_nodes])) if hbnet.mna.n_nodes else 0.0
            if mx > vlim:
                step = step * (vlim / mx)
            xx = xx + step
            if np.max(np.abs(dx)) < newton_tol:
                break
        x = xx
        f0v = f_all(x)
        b0 = b1
        t = t1
        if i >= N - keep:
            j = i - (N - keep)
            buf[j] = x
            tb[j] = t1
    return tb, buf
