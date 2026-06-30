"""Closed-form analytic oracles for first/second-order linear circuits.

These are the strongest references in the suite: the exact response from
circuit theory, independent of any simulator, accurate to machine precision.
They set the true accuracy floor of the DPSpice solver. Each function returns
the analytic node voltage sampled on a caller-supplied time grid.

All sources are ``SINE(0 Vm f)`` — zero offset, zero phase — started from rest
(all initial conditions zero), matching the netlists emitted by
:mod:`dpspice.validation.families`.
"""
from __future__ import annotations

import numpy as np

TWO_PI = 2.0 * np.pi


def rc_lowpass(t: np.ndarray, Vm: float, f: float, R: float, C: float) -> np.ndarray:
    """Output across C of a series R-C driven by ``Vm sin(2 pi f t)``, v_C(0)=0.

    v_C(t) = Vm/sqrt(1+(wRC)^2) [ sin(wt - phi) + sin(phi) e^{-t/RC} ],
    phi = atan(wRC). Exact.
    """
    w = TWO_PI * f
    tau = R * C
    amp = Vm / np.sqrt(1.0 + (w * tau) ** 2)
    phi = np.arctan(w * tau)
    return amp * (np.sin(w * t - phi) + np.sin(phi) * np.exp(-t / tau))


def rl_output(t: np.ndarray, Vm: float, f: float, R: float, L: float) -> np.ndarray:
    """Voltage across L (node between R and L-to-ground) of a series R-L,
    driven by ``Vm sin(wt)``, i(0)=0.

    i(t) = Vm/|Z| [ sin(wt - phi) + sin(phi) e^{-t/tau} ],
    |Z| = sqrt(R^2+(wL)^2), phi = atan(wL/R), tau = L/R.
    v_L(t) = Vm sin(wt) - R i(t). Exact.
    """
    w = TWO_PI * f
    Z = np.hypot(R, w * L)
    phi = np.arctan2(w * L, R)
    tau = L / R
    i = (Vm / Z) * (np.sin(w * t - phi) + np.sin(phi) * np.exp(-t / tau))
    return Vm * np.sin(w * t) - R * i


def series_rlc_vc(t: np.ndarray, Vm: float, f: float,
                  R: float, L: float, C: float) -> np.ndarray:
    """Capacitor voltage of a series R-L-C driven by ``Vm sin(wt)``,
    v_C(0)=0, i(0)=0.

    LC v_C'' + RC v_C' + v_C = Vm sin(wt).
    Steady state from the phasor transfer function; transient from the two
    natural roots with the two zero initial conditions. Exact (machine
    precision), valid for under-, over-, and critically-damped cases.
    """
    w = TWO_PI * f
    # Particular (steady-state) solution via the phasor transfer function.
    Hc = 1.0 / (1.0 - w * w * L * C + 1j * w * R * C)
    mag = np.abs(Hc)
    ang = np.angle(Hc)

    def v_ss(tt):
        return Vm * mag * np.sin(w * tt + ang)

    def vp_ss(tt):
        return Vm * mag * w * np.cos(w * tt + ang)

    # Natural roots of LC s^2 + RC s + 1 = 0.
    a, b, c = L * C, R * C, 1.0
    disc = complex(b * b - 4 * a * c)
    s1 = (-b + np.sqrt(disc)) / (2 * a)
    s2 = (-b - np.sqrt(disc)) / (2 * a)

    # Initial conditions: v(0)=0, v'(0)=0  ->  solve for C1, C2.
    #   C1 + C2          = -v_ss(0)
    #   s1 C1 + s2 C2    = -vp_ss(0)
    rhs0 = -v_ss(0.0)
    rhs1 = -vp_ss(0.0)
    if np.isclose(s1, s2):
        # Critically damped: v_h = (C1 + C2 t) e^{s1 t}.
        C1 = rhs0
        C2 = rhs1 - s1 * C1
        v_h = (C1 + C2 * t) * np.exp(s1 * t)
    else:
        det = s2 - s1
        C1 = (rhs0 * s2 - rhs1) / det
        C2 = (rhs1 - rhs0 * s1) / det
        v_h = C1 * np.exp(s1 * t) + C2 * np.exp(s2 * t)
    return np.real(v_ss(t) + v_h)
