"""Analytic Fourier coefficients of the two-level switch gate.

A routed gated switch contributes a time-periodic conductance

    g(t) = g_off + (g_on - g_off) * s(t)

where s(t) is the unit square gate: 1 on [phase*T, (phase+duty)*T) of each
switching period T, 0 otherwise. Its two-sided Fourier coefficients are known
in closed form:

    S_0 = d
    S_k = e^{-j pi k d} * sin(pi k d) / (pi k) * e^{-2j pi k * phase}   (k != 0)

The closed form is exact for every harmonic, so the LTP and envelope paths use
it instead of an FFT of sampled gates (which carries O(1/N) aliasing error at
the gate discontinuities). The hybrid NR-HB path instead samples the gate on
the solver's AFT collocation grid (see :func:`gate_samples`) so the constant
switch block is consistent with the sampled diode residual to solver precision.
"""
from __future__ import annotations

import numpy as np


def gate_coeffs(duty: float, phase: float, Kc: int) -> np.ndarray:
    """Two-sided coefficients S_{-Kc..Kc} of the unit gate, DC at index Kc."""
    S = np.zeros(2 * Kc + 1, dtype=complex)
    S[Kc] = duty
    for k in range(1, Kc + 1):
        c = np.exp(-1j * np.pi * k * duty) * np.sin(np.pi * k * duty) / (np.pi * k)
        c *= np.exp(-2j * np.pi * k * phase)
        S[Kc + k] = c
        S[Kc - k] = np.conj(c)      # real gate: S_{-k} = conj(S_k)
    return S


def conductance_coeffs(duty: float, phase: float,
                       g_on: float, g_off: float, Kc: int) -> np.ndarray:
    """Two-sided coefficients of g(t) = g_off + (g_on - g_off) s(t)."""
    Gk = (g_on - g_off) * gate_coeffs(duty, phase, Kc)
    Gk[Kc] += g_off
    return Gk


def gate_samples(N: int, duty: float, phase: float,
                 g_on: float, g_off: float) -> np.ndarray:
    """g(t) sampled on the AFT grid t_i = i*T/N (hybrid-path convention)."""
    tg = np.arange(N) / N
    return np.where(np.mod(tg - phase, 1.0) < duty, g_on, g_off)
