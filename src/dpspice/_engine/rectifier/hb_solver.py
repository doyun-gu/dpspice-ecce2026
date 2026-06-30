"""
Harmonic-balance solvers for the rectifier (build spec sections 1.4, 2).

Both routes solve the steady-state periodic operating point directly (no
cycle-by-cycle settling), reusing the engine's per-harmonic linear blocks
Y_k = A - j k w0 E (mna.HBNet) and the AFT primitives (aft.py).

Residual (engine convention  E x' = A x + b + f_nl(x)):
    F(X) = Y_big X + B_big + Fnl(X) = 0
where Y_big = blkdiag(Y_{-K..K}), B_big = stacked source harmonics, and Fnl(X)
are the stacked harmonics of the device current injection f_nl(x(t)).

  Route B  solve_newton    Shockley diode, AFT Newton with the exact spectral
                           Jacobian J = Y_big + sum_d kron(Toeplitz(G_d), P_d),
                           plus source-stepping, K-continuation, damped step.
  Route A  solve_ideal     Ideal diode as a self-consistent time-varying
                           conductance: outer fixed point on the conduction
                           window, each iteration a single linear HB solve.

Stacked layout: harmonic-major, blocks k=-K..K, each of length n=hbnet.n.
The diode Jacobian in this layout is kron(T, P): entry [(a,p),(b,q)] = T[a,b] P[p,q].

Author: Doyun Gu (University of Manchester) -- ECCE 2026 nonlinear extension.
"""
import numpy as np
import aft
from mna import HBNet, stack_to_nodes, nodes_to_stack
from reference_td import Diode


class HBResult:
    def __init__(self, Xn, K, w0, hbnet, iters, residual, converged, route):
        self.Xn = Xn              # (n, H) per-node harmonic coefficients
        self.K = K
        self.w0 = w0
        self.hbnet = hbnet
        self.iters = iters
        self.residual = residual
        self.converged = converged
        self.route = route

    def waveform(self, node, N=None):
        """Reconstruct v_node(t) over one period on N samples; returns (t, v)."""
        if N is None:
            N = aft.oversample_N(self.K)
        i = self.hbnet.idx(node)
        v = aft.to_time(self.Xn[i], self.K, N)
        T0 = 2 * np.pi / self.w0
        t = np.arange(N) / N * T0
        return t, v

    def harmonics(self, node):
        """Two-sided harmonic coefficients of a node voltage (length 2K+1)."""
        return self.Xn[self.hbnet.idx(node)]


# ---------------------------------------------------------------------------
# shared pieces
# ---------------------------------------------------------------------------
def _source_stack(hbnet, w0, K, N):
    Bk = hbnet.source_coeffs(w0, K, N)               # (n, H)
    return np.concatenate([Bk[:, a] for a in range(2 * K + 1)]), Bk


def _nodes_time(X, K, n, N):
    """stacked X -> per-node time samples v(t) (n, N)."""
    Xn = stack_to_nodes(X, K, n)
    return aft.to_time(Xn, K, N), Xn


def _diode_current_time(diodes, vt):
    """Time-domain current injection f(t) and conductance g(t) per diode.
    vt: (n, N).  Returns f(t) (n, N) and list of (diode, g_t)."""
    n, N = vt.shape
    f = np.zeros((n, N))
    glist = []
    for d in diodes:
        va = vt[d.ia] if d.ia >= 0 else np.zeros(N)
        vb = vt[d.ib] if d.ib >= 0 else np.zeros(N)
        vd = va - vb
        i = d.law.I(vd)
        if d.ia >= 0:
            f[d.ia] -= i
        if d.ib >= 0:
            f[d.ib] += i
        glist.append((d, d.law.dIdV(vd)))
    return f, glist


def _diode_jacobian(glist, K, n, N):
    """Sum_d kron(Toeplitz(G_d), P_d) in harmonic-major layout."""
    H = 2 * K + 1
    J = np.zeros((H * n, H * n), dtype=complex)
    for d, g_t in glist:
        Gk = aft.to_freq(g_t, K, N, Kc=2 * K)        # +-2K harmonics of g(t)
        T = aft.toeplitz_block(Gk, K)                # (H, H)
        J += np.kron(T, d.P.astype(complex))
    return J


# ---------------------------------------------------------------------------
# Route B: Shockley diode, AFT Newton
# ---------------------------------------------------------------------------
def solve_newton(hbnet: HBNet, diodes, f0, K, N=None,
                 amp_steps=4, k_continuation=True, tol=1e-10,
                 max_iter=60, verbose=False):
    """AFT Newton with source-stepping + K-continuation + damped step."""
    if isinstance(diodes, Diode):
        diodes = [diodes]
    w0 = 2 * np.pi * f0
    n = hbnet.n
    if N is None:
        N = aft.oversample_N(K)

    # K-continuation: solve at K1=1 first, zero-pad up to K
    Korder = [1, K] if (k_continuation and K > 1) else [K]
    X = None
    total_iters = 0
    for Ki in Korder:
        Ni = aft.oversample_N(Ki)
        Yb, ks = hbnet.assemble_linear(w0, Ki)
        Bfull, _ = _source_stack(hbnet, w0, Ki, Ni)
        if X is None:
            X = np.linalg.solve(Yb, -Bfull * 0.0)    # zero start (diode off)
        else:
            X = _zero_pad(X, Kprev, Ki, n)
        # source-stepping: ramp the excitation amplitude
        alphas = np.linspace(1.0 / amp_steps, 1.0, amp_steps) if amp_steps > 1 else [1.0]
        for alpha in alphas:
            B = Bfull * alpha
            for it in range(max_iter):
                vt, _ = _nodes_time(X, Ki, n, Ni)
                f_t, glist = _diode_current_time(diodes, vt)
                Fnl = nodes_to_stack(aft.to_freq(f_t, Ki, Ni), Ki, n)
                F = Yb @ X + B + Fnl
                res = np.max(np.abs(F))
                if res < tol:
                    break
                J = Yb + _diode_jacobian(glist, Ki, n, Ni)
                dX = np.linalg.solve(J, -F)
                X = _damped_update(X, dX, Yb, B, hbnet, diodes, Ki, n, Ni)
                total_iters += 1
            if verbose:
                print(f"  K={Ki} alpha={alpha:.2f} res={res:.2e} iters~{it}")
        Kprev = Ki
    Xn = stack_to_nodes(X, K, n)
    converged = res < tol * 100
    return HBResult(Xn, K, w0, hbnet, total_iters, res, converged, "newton")


def _damped_update(X, dX, Yb, B, hbnet, diodes, K, n, N):
    """Backtracking line search on ||F|| (pnjlim-style robustness)."""
    def resid(Xc):
        vt, _ = _nodes_time(Xc, K, n, N)
        f_t, _ = _diode_current_time(diodes, vt)
        Fnl = nodes_to_stack(aft.to_freq(f_t, K, N), K, n)
        return np.max(np.abs(Yb @ Xc + B + Fnl))
    r0 = resid(X)
    for alpha in (1.0, 0.5, 0.25, 0.125):
        Xc = X + alpha * dX
        if resid(Xc) < r0:
            return Xc
    return X + 0.125 * dX


def _zero_pad(X, Kold, Knew, n):
    """Embed a K=Kold solution into a K=Knew stacked vector (new harmonics 0)."""
    Xn = stack_to_nodes(X, Kold, n)                  # (n, 2Kold+1)
    H = 2 * Knew + 1
    Xn2 = np.zeros((n, H), dtype=complex)
    off = Knew - Kold
    Xn2[:, off:off + (2 * Kold + 1)] = Xn
    return nodes_to_stack(Xn2, Knew, n)


# ---------------------------------------------------------------------------
# Route A: ideal diode as a self-consistent time-varying conductance
# ---------------------------------------------------------------------------
def solve_ideal(hbnet: HBNet, diodes, f0, K, N=None, tol=1e-8,
                max_outer=60, relax=0.5, verbose=False):
    """Outer fixed point on the conduction window.  Each iteration: from the
    current v(t) set g(t) per diode (g_on where forward-biased), solve the
    single linear HB system [Y_big + sum kron(Toeplitz(G),P)] X = -B."""
    if isinstance(diodes, Diode):
        diodes = [diodes]
    w0 = 2 * np.pi * f0
    n = hbnet.n
    if N is None:
        N = aft.oversample_N(K)
    Yb, ks = hbnet.assemble_linear(w0, K)
    Bfull, _ = _source_stack(hbnet, w0, K, N)

    X = np.linalg.solve(Yb, -Bfull)                  # linear start (diode ~open)
    g_prev = None
    for outer in range(max_outer):
        vt, _ = _nodes_time(X, K, n, N)
        # build g(t) from each diode's conduction state
        glist = []
        gcat = []
        for d in diodes:
            va = vt[d.ia] if d.ia >= 0 else np.zeros(N)
            vb = vt[d.ib] if d.ib >= 0 else np.zeros(N)
            g_t = d.law.dIdV(va - vb)                # piecewise g_on/g_off
            glist.append((d, g_t))
            gcat.append(g_t)
        gcat = np.concatenate(gcat)
        if g_prev is not None:
            gcat = relax * gcat + (1 - relax) * g_prev   # under-relax the window
            # rebuild glist from relaxed g
            glist = [(d, gcat[i * N:(i + 1) * N]) for i, d in enumerate(diodes)]
        Jd = _diode_jacobian(glist, K, n, N)
        Xnew = np.linalg.solve(Yb + Jd, -Bfull)
        dX = np.max(np.abs(Xnew - X))
        X = Xnew
        g_prev = gcat
        if verbose:
            print(f"  outer={outer} dX={dX:.2e}")
        if dX < tol:
            break
    Xn = stack_to_nodes(X, K, n)
    return HBResult(Xn, K, w0, hbnet, outer + 1, dX, dX < tol * 100, "ideal")
