"""
Nonlinear device laws for the rectifier demos.

Each device exposes the SPICE companion-model pair:
    I(v)    terminal current through the device  (anode -> cathode)
    dIdV(v) small-signal conductance  g = dI/dV  at the operating point

That is the entire interface the harmonic-balance Newton step and the
time-domain reference need (build spec 1.4, 2): at every operating point the
device linearises to a conductance g in parallel with an equivalent current
source, which stamps into MNA exactly like a resistor.  Swapping I/dIdV is all
it takes to go diode -> MOSFET -> saturable transformer later.

Engine sign convention: positive current flows anode -> cathode, i.e. out of
the anode node and into the cathode node (handled by the caller's incidence P).

Author: Doyun Gu (University of Manchester) -- ECCE 2026 nonlinear extension.
"""
import numpy as np

_EXP_CLIP = 40.0   # clamp v/(nVT) so exp() never overflows (pnjlim-style guard)


class ShockleyDiode:
    """Exponential diode  I(v) = Is (exp(v/(n VT)) - 1) + gmin v.

    The +gmin v term keeps the conductance strictly positive (no singular
    MNA when the junction is hard off), matching SPICE's gmin.  Route B."""

    def __init__(self, Is=1e-14, n=1.0, VT=0.025852, gmin=1e-12):
        self.Is = Is
        self.n = n
        self.VT = VT
        self.gmin = gmin

    def _x(self, v):
        return np.clip(v / (self.n * self.VT), -_EXP_CLIP, _EXP_CLIP)

    def I(self, v):
        return self.Is * (np.exp(self._x(v)) - 1.0) + self.gmin * v

    def dIdV(self, v):
        return (self.Is / (self.n * self.VT)) * np.exp(self._x(v)) + self.gmin


class IdealDiode:
    """Piecewise-linear ideal diode: conductance g_on when forward-biased
    (v > 0), g_off when blocking.  No junction drop.  Route A.

    g_on = 1/R_on (small R_on), g_off = small leakage (never exactly 0)."""

    def __init__(self, R_on=1e-3, g_off=1e-9):
        self.g_on = 1.0 / R_on
        self.g_off = g_off

    def _g(self, v):
        return np.where(np.asarray(v) > 0.0, self.g_on, self.g_off)

    def I(self, v):
        return self._g(v) * v

    def dIdV(self, v):
        return self._g(v)
