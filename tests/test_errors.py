"""Section 3 - error-handling catalogue.

Every adversarial input below must produce a clean, actionable ``DpspiceError``
that names the fix (the flag, card, or env var the user needs), never a raw
traceback. The asserted message fragments were captured from the real engine,
not assumed. The catalogue is exercised through the public API path; the CLI
and MCP layers wrap the same ``run``/``validate`` calls, so a DpspiceError here
becomes a clean nonzero exit (CLI) and an ``{"error": ...}`` payload (MCP).
"""
from __future__ import annotations

import sys
import types

import pytest

from dpspice import api, crossval
from dpspice.dispatch import DpspiceError, analyze


def _expect(netlist, fragment, **run_kw):
    with pytest.raises(DpspiceError) as ei:
        api.load(netlist).run(**run_kw)
    msg = str(ei.value)
    assert fragment.lower() in msg.lower(), f"message {msg!r} missing {fragment!r}"
    return msg


def test_empty_netlist():
    _expect("", "empty or unparseable")


def test_whitespace_only_netlist():
    _expect("   \n\t\n  ", "empty or unparseable")


def test_no_tran_card_names_tran():
    msg = _expect("* x\nV1 in 0 SINE(0 1 50)\nR1 in 0 1k\n.end\n", ".tran")
    assert "--mode hb" in msg  # offers the steady-state alternative


def test_unparseable_value_names_component_and_notation():
    msg = _expect("* x\nV1 in 0 SINE(0 1 50)\nR1 in 0 banana\n.tran 0 1m\n.end\n",
                  "numeric value")
    assert "R1" in msg and "1k" in msg  # names the component and the fix notation


def test_malformed_component_arity():
    msg = _expect("* x\nV1 in 0 SINE(0 1 50)\nR1 in\n.tran 0 1m\n.end\n",
                  "3 fields")
    assert "R1" in msg


def test_unsupported_device_named():
    msg = _expect("* x\nV1 in 0 SINE(0 1 50)\nQ1 a b c npn\nR1 in 0 1k\n"
                  ".tran 0 1m\n.end\n", "unsupported nonlinear")
    assert "Q1" in msg


def test_oversized_circuit_names_env(monkeypatch):
    monkeypatch.setenv("DPSPICE_MAX_STATES", "2")
    msg = _expect("* x\nV1 in 0 SINE(0 1 50)\nR1 in a 1k\nL1 a b 1m\n"
                  "C1 b 0 1u\nR2 b 0 1k\n.tran 0 1m\n.end\n", "DPSPICE_MAX_STATES")
    assert "states" in msg.lower()


def test_multi_source_omega_is_warning_not_crash():
    """Ambiguous carrier is a recoverable warning that names --omega, not a
    hard error: the dominant source is picked and the run proceeds."""
    multi = ("* x\nV1 a 0 SINE(0 1 50)\nV2 b 0 SINE(0 1 60)\n"
             "R1 a b 1k\nR2 b 0 1k\nL1 a 0 1m\n.tran 0 0.1\n.end\n")
    info = analyze(multi, mode="idp")
    assert any("--omega" in w for w in info.warnings), info.warnings
    assert info.omega_hz == 50.0  # dominant source chosen


def test_hb_nonconvergence_names_harmonics(monkeypatch):
    """Force the HB inner solve to report non-convergence and assert the
    catalogue message points at --harmonics / --tol."""
    import hb_solver

    class _NoConverge:
        converged = False
        residual = 1.0
    monkeypatch.setattr(hb_solver, "solve_newton",
                        lambda *a, **k: _NoConverge(), raising=True)

    rect = ("* x\nV1 in 0 SINE(0 5 50)\nD1 in out Dmod\nR1 out 0 1k\n"
            "C1 out 0 10u\n.model Dmod D(Is=1e-9 N=1)\n.tran 0 1.0 0.96 1u\n.end\n")
    msg = _expect(rect, "--harmonics", mode="hb")
    assert "converge" in msg.lower()


def test_ngspice_missing_names_ref(monkeypatch):
    """With no --ref and ngspice unavailable, validate must name both the
    install hint and the --ref escape hatch."""
    monkeypatch.setattr(crossval, "ngspice_available", lambda: False, raising=True)
    rlc = ("* x\nV1 in 0 SINE(0 1 92300)\nR1 in n2 3.0\nL1 n2 out 100.04u\n"
           "C1 out 0 30.07n\nR2 out 0 2k\n.tran 0 0.2m\n.end\n")
    with pytest.raises(DpspiceError) as ei:
        crossval.validate(rlc, ref=None)
    msg = str(ei.value)
    assert "--ref" in msg and "ngspice" in msg.lower()
