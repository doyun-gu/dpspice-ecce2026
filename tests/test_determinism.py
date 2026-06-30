"""Section 2 - determinism guarantee.

The public solver path is deterministic: the same netlist run repeatedly
returns bit-identical waveforms. We assert exact equality (epsilon = 0) for the
adaptive default path. The determinism contract is documented in README.md and
CONTRIBUTING.md; this test is its executable form.

If a future adaptive/randomised path is added that can only guarantee a small
documented epsilon, give it its own entry with that epsilon rather than
loosening the bound here.
"""
from __future__ import annotations

import numpy as np
import pytest

from dpspice import api

_N = 5

_RLC = ("* Series RLC\nV1 in 0 SINE(0 1 92300)\nR1 in n2 3.0\n"
        "L1 n2 out 100.04u\nC1 out 0 30.07n\nR2 out 0 2k\n.tran 0 0.2m\n.end\n")

_RECT = ("* Half-wave rectifier\nV1 in 0 SINE(0 5 50)\nD1 in out Dmod\n"
         "R1 out 0 1k\nC1 out 0 10u\n.model Dmod D(Is=1e-9 N=1)\n"
         ".tran 0 1.0 0.96 1u\n.end\n")

_WPT = ("* WPT k=0.2\nV1 in 0 SINE(0 10 50329.2)\nRs in a 1\nC1 a b 1e-07\n"
        "L1 b 0 1e-4\nL2 c 0 1e-4\nC2 c out 1e-07\nRload out 0 10\n"
        "K1 L1 L2 0.2\n.tran 0 0.000794767\n.end\n")


def _stack(netlist, mode="auto"):
    """Concatenate every waveform of one run into a single vector."""
    r = api.load(netlist).run(mode=mode)
    return np.concatenate([np.asarray(w.v, dtype=float) for w in r.waveforms])


@pytest.mark.parametrize("name,netlist", [
    ("rlc", _RLC),
    ("rectifier", _RECT),
    ("wpt", _WPT),
])
def test_bit_identical_across_runs(name, netlist):
    first = _stack(netlist)
    assert first.size > 0, f"{name}: no waveform produced"
    for i in range(1, _N):
        again = _stack(netlist)
        assert again.shape == first.shape, (
            f"{name}: run {i} changed sample count {first.shape}->{again.shape}"
        )
        max_diff = float(np.max(np.abs(again - first)))
        assert max_diff == 0.0, (
            f"{name}: run {i} differs from run 0 by {max_diff:.3e} "
            f"(expected bit-identical; the public solver path must be deterministic)"
        )
