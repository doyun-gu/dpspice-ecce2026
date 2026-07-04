"""Transient envelope bank for routed switched netlists.

The same constant matrices that give the LTP steady state also define a bank
ODE over the stacked harmonic envelopes X = (x~_{-K}, ..., x~_{K}):

    E_big dX/dt = A_big X + B_big

with E_big = blkdiag(E), A_big the switched-HB operator (diagonal shift blocks
plus the constant switch Toeplitz coupling) and B_big the stacked source
harmonics. Start-up and load-step dynamics of the envelopes come out of a
fixed-step trapezoidal integration whose step matrices are prefactored once
per segment -- the carrier never has to be resolved.

E_big is singular (MNA is a DAE), so each segment opens with ``be_open``
backward-Euler steps: pure trapezoidal from an inconsistent state (rest, or
the pre-event state after a topology swap) leaves an undamped (-1)^n Nyquist
oscillation on the algebraic variables. One L-stable BE step re-initialises
them consistently; trapezoidal then integrates the envelope dynamics with its
usual second-order accuracy. This is why the package's adaptive (scipy) state
integrators are not used here: they require a regular E.

Piecewise-constant topology events (e.g. a load step) are supported by
re-stamping the constant matrices from a modified netlist and swapping the
prefactored bank at the grid point nearest the event time.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np

from .. import _engine  # noqa: F401  side-effect: flat engine modules on sys.path

from .ltp import SwitchedHBNet, source_stack

Bank = Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]  # Eb, Ab, Bb, ks


def assemble_bank(swnet: SwitchedHBNet, f_sw: float, K: int) -> Bank:
    """Constant bank matrices (Eb, Ab, Bb, ks) of a routed switched network."""
    w0 = 2 * np.pi * f_sw
    Ab, ks = swnet.assemble_linear(w0, K)
    Eb = np.kron(np.eye(2 * K + 1, dtype=complex), swnet.E)
    Bb = source_stack(swnet, w0, K)
    return Eb, Ab, Bb, ks


def integrate(bank: Bank, t_end: float, n_steps: int,
              events: Optional[Sequence[Tuple[float, Bank]]] = None,
              X0: Optional[np.ndarray] = None, be_open: int = 1):
    """Integrate the bank ODE from X0 (default rest).

    events: optional [(t_event, bank_after)] list; at the grid point nearest
    each t_event the prefactored matrices are swapped and the new segment is
    re-opened with BE steps. Returns (t, traj, ks) with traj shaped
    (n_steps+1, 2K+1, n).
    """
    Eb, Ab, Bb, ks = bank
    nB = Eb.shape[0]
    n = nB // len(ks)
    dt = t_end / n_steps

    def factor(bk: Bank):
        Eb_, Ab_, Bb_, _ = bk
        M = Eb_ - 0.5 * dt * Ab_
        trap = (np.linalg.solve(M, Eb_ + 0.5 * dt * Ab_),
                np.linalg.solve(M, dt * Bb_))
        Mbe = Eb_ - dt * Ab_
        be = (np.linalg.solve(Mbe, Eb_), np.linalg.solve(Mbe, dt * Bb_))
        return trap, be

    swap_at = {}
    for t_event, bk in (events or []):
        i = int(round(t_event / dt))
        if not 0 <= i <= n_steps:
            raise ValueError(f"event time {t_event:g} s outside [0, {t_end:g}]")
        swap_at[i] = bk

    trap, be = factor(bank)
    X = (np.zeros(nB, dtype=complex) if X0 is None
         else np.asarray(X0, dtype=complex).reshape(-1))
    traj = np.zeros((n_steps + 1, len(ks), n), dtype=complex)
    traj[0] = X.reshape(len(ks), n)
    seg_start = 0
    for i in range(n_steps):
        if i in swap_at:
            trap, be = factor(swap_at[i])
            seg_start = i               # re-open the new segment with BE
        stepA, stepb = be if (i - seg_start) < be_open else trap
        X = stepA @ X + stepb
        traj[i + 1] = X.reshape(len(ks), n)
    t = np.linspace(0.0, t_end, n_steps + 1)
    return t, traj, ks


def reconstruct(traj: np.ndarray, ks: np.ndarray, t: np.ndarray,
                w0: float, idx: int) -> np.ndarray:
    """Carrier reconstruction of one state from an envelope trajectory:
    x(t_i) = Re sum_a traj[i, a, idx] e^{j ks[a] w0 t_i}."""
    ph = np.exp(1j * np.outer(t, ks * w0))              # (nt, nH)
    return np.einsum("ik,ik->i", ph, traj[:, :, idx]).real
