"""
Harmonic-balance MNA wrapper around the DPSpice engine.

Reuses the engine's automatic MNA build (parse_ltspice_netlist -> build_mna),
then assembles the multi-harmonic linear network as a stack of per-harmonic
blocks.  The single-frequency DPSpice shift  A - j w E  generalises to

        Y_k = A - j k w0 E            (linear block for harmonic k)

so the linear part of the rectifier solver is block-diagonal in k and reuses
the exact stamping the engine already does (build spec 1.2).  The diode adds
off-diagonal coupling on top (handled in hb_solver, not here).

Stacked layout (matches mhdp.py): harmonic-major, blocks ordered k=-K..K,
each block of length n = mna.n_total.  Vector length = (2K+1) * n.

Sign convention VERIFIED against the analytic RC transfer function in
tests/test_linear_rc.py:  solve (A - j k w E) X_k = -B_k, with
B_k = FFT(b(t).copy())/N.  b_func returns a SHARED buffer, so .copy() is
mandatory when sampling it.

Author: Doyun Gu (University of Manchester) -- ECCE 2026 nonlinear extension.
"""
import os
import sys
import numpy as np

# --- engine path (resolved, not hard-coded) --------------------------------
_HOME = os.path.expanduser("~")
_ENGINE = os.path.join(_HOME, "Developer/dynamic-phasors/DPSpice-com/src/python")
if _ENGINE not in sys.path:
    sys.path.insert(0, _ENGINE)
from netlist_parser import parse_ltspice_netlist   # noqa: E402
from mna_circuit import build_mna                   # noqa: E402

from aft import n_harmonics                          # noqa: E402


class HBNet:
    """Linear network for harmonic balance, built from an LTspice netlist."""

    def __init__(self, netlist_str):
        self.netlist = parse_ltspice_netlist(netlist_str)
        self.mna = build_mna(self.netlist)
        self.n = self.mna.n_total
        self.E = self.mna.E.astype(complex)
        self.A = self.mna.A.astype(complex)

    # -- node / state lookups ------------------------------------------------
    def idx(self, node_name):
        """State index of a node voltage (>=0), or -1 for ground."""
        return self.mna.node_map.idx(node_name)

    @property
    def labels(self):
        return self.mna.state_labels

    # -- source harmonics ----------------------------------------------------
    def source_coeffs(self, w0, K, N):
        """Two-sided Fourier coefficients B_k of the engine excitation b(t),
        one fundamental period T0 = 2*pi/w0 sampled at N points.
        Returns an (n, 2K+1) complex array (harmonics on the last axis)."""
        T0 = 2.0 * np.pi / w0
        tg = np.arange(N) / N * T0
        b = np.array([self.mna.b_func(t).copy() for t in tg])   # (N, n); .copy()!
        F = np.fft.fft(b, axis=0) / N                            # (N, n)
        dc = K
        Bk = np.zeros((self.n, n_harmonics(K)), dtype=complex)
        Bk[:, dc] = F[0]
        for k in range(1, K + 1):
            Bk[:, dc + k] = F[k]
            Bk[:, dc - k] = F[N - k]
        return Bk

    # -- linear blocks -------------------------------------------------------
    def block(self, k, w0):
        """Per-harmonic linear MNA block Y_k = A - j k w0 E."""
        return self.A - 1j * k * w0 * self.E

    def assemble_linear(self, w0, K):
        """Block-diagonal stack Y_big = blkdiag(Y_{-K}, ..., Y_{K}) and the
        stacked source RHS  rhs = -B_big  for the decoupled linear solve."""
        H = n_harmonics(K)
        n = self.n
        Yb = np.zeros((H * n, H * n), dtype=complex)
        ks = np.arange(-K, K + 1)
        for a, k in enumerate(ks):
            Yb[a * n:(a + 1) * n, a * n:(a + 1) * n] = self.block(k, w0)
        return Yb, ks

    def solve_linear(self, w0, K, N):
        """Solve the purely linear network (no diode) in harmonic balance.
        Returns the stacked solution X (length H*n) and ks."""
        Yb, ks = self.assemble_linear(w0, K)
        Bk = self.source_coeffs(w0, K, N)            # (n, H)
        # stacked RHS, harmonic-major: block a = harmonic ks[a]
        rhs = np.concatenate([-Bk[:, a] for a in range(len(ks))])
        X = np.linalg.solve(Yb, rhs)
        return X, ks


# ---------------------------------------------------------------------------
# stacked <-> per-node reshaping helpers
# ---------------------------------------------------------------------------
def stack_to_nodes(X, K, n):
    """(H*n,) harmonic-major stacked vector -> (n, H) per-node coefficients,
    harmonics ordered k=-K..K on the last axis."""
    H = n_harmonics(K)
    return X.reshape(H, n).T.copy()


def nodes_to_stack(Xn, K, n):
    """(n, H) per-node coefficients -> (H*n,) harmonic-major stacked vector."""
    return Xn.T.reshape(-1).copy()
