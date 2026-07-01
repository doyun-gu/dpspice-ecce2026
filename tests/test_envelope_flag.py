"""``dpspice run --envelope`` — export the IDP phasor-magnitude envelope.

The phasor solver has always computed the magnitude envelope |X(t)| internally
(``res['envelopes']``) but dropped it at the API boundary; downstream figures
had to reach into ``dpspice._engine`` to get it. The flag makes the same data
available over the public CLI/API so envelope plots are reproducible without
internal-API access. These tests pin the contract:

- off by default (payload shape unchanged, no 'envelopes' key);
- on for IDP: one envelope per exported signal, on the waveform time grid,
  and it bounds the reconstructed instantaneous waveform;
- td/hb: no fabricated envelope — ``envelopes`` stays None plus a warning.
"""
from __future__ import annotations

import numpy as np
import pytest

import dpspice


@pytest.fixture(scope="module")
def rlc_text():
    return dpspice.example_text("rlc.sp")


def test_default_off(rlc_text):
    res = dpspice.run(rlc_text)
    assert res.envelopes is None
    assert "envelopes" not in res.to_dict(include_waveforms=True)


def test_idp_envelope_exported(rlc_text):
    res = dpspice.run(rlc_text, with_envelopes=True)
    assert res.solver == "idp"
    assert res.envelopes, "IDP run with with_envelopes=True must export envelopes"

    d = res.to_dict(include_waveforms=True)
    assert [e["name"] for e in d["envelopes"]]

    # The envelope must live on the same decimated grid as the waveforms and
    # bound the reconstructed instantaneous signal it belongs to.
    waves = {w.name: w for w in res.waveforms}
    for env in res.envelopes:
        if env.name not in waves:      # I(...) envelopes have no V waveform twin
            continue
        w = waves[env.name]
        assert env.t == w.t
        v = np.asarray(w.v)
        e = np.asarray(env.v)
        assert np.all(e >= 0)
        tol = 1e-6 * max(1.0, float(np.max(e)))
        assert np.all(np.abs(v) <= e + tol), f"{env.name}: |v| exceeds envelope"


def test_td_ignores_with_warning(rlc_text):
    res = dpspice.run(rlc_text, mode="td", with_envelopes=True)
    assert res.envelopes is None
    assert any("--envelope" in w for w in res.warnings)


def test_hb_ignores_with_warning():
    res = dpspice.run(dpspice.example_text("rectifier_halfwave.sp"),
                      with_envelopes=True)
    assert res.solver == "hb"
    assert res.envelopes is None
    assert any("--envelope" in w for w in res.warnings)
