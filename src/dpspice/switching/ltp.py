"""LTP (linear time-periodic) steady state for routed switched netlists.

A gated switch has a conductance g(t) that is known before the solve, so its
harmonic-balance contribution is a CONSTANT Toeplitz block

    J_sw = Toeplitz(G_k) (x) P_sw

folded into the linear operator once. A circuit whose only switching devices
are gated switches therefore needs no Newton iteration at all: the periodic
steady state is ONE linear solve of the (2K+1)n block system.

:class:`SwitchedHBNet` extends the existing rectifier HB network with the
switch blocks by overriding ``assemble_linear`` -- everything else (source
harmonics, AFT primitives, the Newton solver for hybrid circuits) is reused
unchanged. Gate coefficients are analytic by default (exact); the hybrid
NR-HB path samples the gate on the solver's AFT grid instead so the constant
block is consistent with the sampled diode residual (``sampled_gates=True``).
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from .. import _engine  # noqa: F401  side-effect: flat engine modules on sys.path

import aft                              # noqa: E402
import mhdp                             # noqa: E402
from mna import HBNet, stack_to_nodes   # noqa: E402
from hb_solver import HBResult          # noqa: E402

from .gate import conductance_coeffs, gate_samples
from .router import GatedSwitch


class SwitchedHBNet(HBNet):
    """HBNet plus the constant Toeplitz blocks of routed gated switches."""

    def __init__(self, netlist_str: str, switches: List[GatedSwitch],
                 sampled_gates: bool = False):
        super().__init__(netlist_str)
        self.switches = list(switches)
        self.sampled_gates = sampled_gates
        # branch incidence per switch, on the clean network's node indices
        self._P = [mhdp.switch_stamp(self.mna, s.n_pos, s.n_neg)
                   for s in self.switches]

    def switch_block(self, K: int) -> np.ndarray:
        """Sum of the constant switch Toeplitz blocks at harmonic order K."""
        H = 2 * K + 1
        J = np.zeros((H * self.n, H * self.n), dtype=complex)
        N = aft.oversample_N(K)
        for s, P in zip(self.switches, self._P):
            if self.sampled_gates:
                g_t = gate_samples(N, s.duty, s.phase, s.g_on, s.g_off)
                Gk = aft.to_freq(g_t, K, N, Kc=2 * K)
            else:
                Gk = conductance_coeffs(s.duty, s.phase, s.g_on, s.g_off,
                                        Kc=2 * K)
            J += np.kron(aft.toeplitz_block(Gk, K), P)
        return J

    def assemble_linear(self, w0: float, K: int):
        Yb, ks = super().assemble_linear(w0, K)
        if self.switches:
            Yb = Yb + self.switch_block(K)
        return Yb, ks


def source_stack(hbnet: HBNet, w0: float, K: int,
                 N: Optional[int] = None) -> np.ndarray:
    """Stacked source harmonics B_big (harmonic-major, k=-K..K)."""
    if N is None:
        N = aft.oversample_N(K)
    Bk = hbnet.source_coeffs(w0, K, N)
    return np.concatenate([Bk[:, a] for a in range(2 * K + 1)])


def solve_ltp(swnet: SwitchedHBNet, f_sw: float, K: int) -> HBResult:
    """Periodic steady state of a gated-switch circuit: one linear solve."""
    w0 = 2 * np.pi * f_sw
    Yb, ks = swnet.assemble_linear(w0, K)
    Bfull = source_stack(swnet, w0, K)
    X = np.linalg.solve(Yb, -Bfull)
    residual = float(np.max(np.abs(Yb @ X + Bfull)))
    Xn = stack_to_nodes(X, K, swnet.n)
    return HBResult(Xn, K, w0, swnet, iters=0, residual=residual,
                    converged=True, route="ltp")
