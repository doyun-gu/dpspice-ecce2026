"""Switched-linear pipeline: router, gate coefficients, LTP, envelope, hybrid.

Covers the promotion contract for the ``dpspice.switching`` package:

* analytic gate Fourier coefficients agree with a dense FFT of the sampled
  gate train (symmetric and asymmetric duty, nonzero phase);
* the envelope bank settles to the one-shot LTP harmonic-balance steady state
  (the trapezoidal fixed point of a constant linear bank is exact);
* a netlist routed through :func:`route_netlist` solves to the same answer as
  a directly-assembled :class:`SwitchedHBNet` built from hand-written switch
  records (chopper and synchronous buck);
* the router refuses state-dependent and non-PULSE gates with typed errors;
* the hybrid NR-HB path and its clamped warm start reproduce the unmodified
  Newton solver's fixed point;
* the synchronous boost example lands its k = 0 harmonic on Vin / (1 - d)
  within the reported ripple band.
"""
from __future__ import annotations

import numpy as np
import pytest

import dpspice
from dpspice import DpspiceError
from dpspice.switching import (
    GatedSwitch,
    RoutingError,
    SwitchedHBNet,
    assemble_bank,
    integrate,
    route_netlist,
    solve_hybrid,
    solve_ltp,
    warm_start,
)
from dpspice.switching.gate import conductance_coeffs, gate_coeffs, gate_samples

import aft  # noqa: E402  (flat engine module, importable once dpspice is)
import hb_solver as hb  # noqa: E402
from device import ShockleyDiode  # noqa: E402
from mna import nodes_to_stack  # noqa: E402
from reference_td import Diode  # noqa: E402


# ----------------------------------------------------------------------
# Netlists used across the tests
# ----------------------------------------------------------------------

CHOPPER = """* single-switch chopper into a resistive load
V1 in 0 10
R1 in sw 1
S1 sw 0 g 0 SWMOD
Rl sw 0 50
Vg g 0 PULSE(0 1 0 0 0 4u 10u)
.model SWMOD SW(Ron=10m Roff=1Meg Vt=0.5)
.tran 0 1m
.end
"""

BUCK = """* synchronous buck, complementary gate pair
Vin vin 0 12
S1 vin sw g 0 SWMOD
S2 sw 0 gb 0 SWMOD
L1 sw out 100u
C1 out 0 47u
Rl out 0 5
Vg g 0 PULSE(0 1 0 0 0 8u 20u)
Vgb gb 0 PULSE(1 0 0 0 0 8u 20u)
.model SWMOD SW(Ron=1m Roff=1Meg Vt=0.5)
.tran 0 2m
.end
"""

MIXED = """* gated switch feeding a real diode: hybrid NR-HB path
Vs in 0 SINE(0 10 50)
S1 in mid g 0 SWMOD
Rmid mid 0 1e9
D1 mid out DMOD
C1 out 0 100u
Rl out 0 1k
Vg g 0 PULSE(0 1 0 0 0 5m 20m)
.model SWMOD SW(Ron=10m Roff=1Meg Vt=0.5)
.model DMOD D(Is=1e-9 N=1)
.tran 0 100m
.end
"""

STATE_DEPENDENT = """* gate pair sits in the power path -> not LTP-routable
V1 in 0 10
R1 in a 1
S1 a 0 a 0 SWMOD
Rl a 0 50
.model SWMOD SW(Ron=10m Roff=1Meg Vt=0.5)
.tran 0 1m
.end
"""

SINE_GATE = """* gate driven by a SINE source -> no (duty, f_sw, phase) triple
V1 in 0 10
R1 in sw 1
S1 sw 0 g 0 SWMOD
Rl sw 0 50
Vg g 0 SINE(0 1 50k)
.model SWMOD SW(Ron=10m Roff=1Meg Vt=0.5)
.tran 0 1m
.end
"""


# ----------------------------------------------------------------------
# 1. Analytic gate coefficients vs dense FFT
# ----------------------------------------------------------------------

@pytest.mark.parametrize("duty,phase", [
    (0.5, 0.0),          # symmetric
    (0.3, 0.0),          # asymmetric
    (0.3, 0.15),         # asymmetric with phase offset
])
def test_gate_coeffs_match_fft(duty, phase):
    """Closed-form S_k equals the DFT of a densely sampled gate train.

    The DFT of ideal samples aliases the exact coefficients by O(1/N), so at
    N = 2**16 the agreement floor is well below 1e-4.
    """
    Kc = 8
    N = 2 ** 16
    g = gate_samples(N, duty, phase, 1.0, 0.0)
    fft = np.fft.fft(g) / N
    ks = np.arange(-Kc, Kc + 1)
    dft_coeffs = fft[ks % N]
    analytic = gate_coeffs(duty, phase, Kc)
    assert np.max(np.abs(analytic - dft_coeffs)) < 1e-4


def test_conductance_coeffs_scale_and_offset():
    """G_k = g_off*delta_k0 + (g_on - g_off) * S_k."""
    Kc = 6
    g_on, g_off = 100.0, 1e-12
    s = gate_coeffs(0.3, 0.1, Kc)
    c = conductance_coeffs(0.3, 0.1, g_on, g_off, Kc)
    expected = (g_on - g_off) * s
    expected[Kc] += g_off
    assert np.max(np.abs(c - expected)) < 1e-12


def test_complement_gate_folds_to_complement_duty():
    """An inverted PULSE train routes as duty' = 1 - duty with the phase
    shifted by the ON interval, and the analytic coefficients satisfy
    S'_k = -S_k for k != 0."""
    rt = route_netlist(BUCK)
    s1, s2 = rt.switches
    assert s1.duty == pytest.approx(0.4)
    assert s2.duty == pytest.approx(0.6)
    assert s2.phase == pytest.approx(0.4)
    Kc = 5
    c1 = gate_coeffs(s1.duty, s1.phase, Kc)
    c2 = gate_coeffs(s2.duty, s2.phase, Kc)
    ac = np.delete(c1 + c2, Kc)          # k != 0 terms must cancel
    assert np.max(np.abs(ac)) < 1e-14
    assert (c1[Kc] + c2[Kc]).real == pytest.approx(1.0)


# ----------------------------------------------------------------------
# 2. Envelope steady state vs one-shot LTP harmonic balance
# ----------------------------------------------------------------------

def test_envelope_settles_to_ltp_steady_state():
    """After the LC transient dies, every envelope harmonic matches the
    one-shot LTP solve to better than 1e-6 relative (trapezoidal fixed point
    of a constant linear bank is the exact steady state)."""
    K = 5
    rt = route_netlist(BUCK)
    net = SwitchedHBNet(rt.clean_netlist, rt.switches)
    res = solve_ltp(net, rt.f_sw, K)

    bank = assemble_bank(net, rt.f_sw, K)
    t_end, n_steps = 20e-3, 10000     # ~40 L-C time constants
    t, traj, ks = integrate(bank, t_end, n_steps)
    Xf = traj[-1].T                   # (n, 2K+1)

    scale = np.max(np.abs(res.Xn))
    err = np.max(np.abs(Xf - res.Xn)) / scale
    assert err < 1e-6, f"envelope end state off by {err:.2e} relative"


# ----------------------------------------------------------------------
# 3. Routed netlist == directly-assembled solve
# ----------------------------------------------------------------------

def _direct_solve(clean_netlist, switches, f_sw, K):
    net = SwitchedHBNet(clean_netlist, switches)
    return solve_ltp(net, f_sw, K)


def test_routed_chopper_matches_direct_assembly():
    K = 15
    rt = route_netlist(CHOPPER)
    routed = solve_ltp(SwitchedHBNet(rt.clean_netlist, rt.switches), rt.f_sw, K)

    clean = """V1 in 0 10
R1 in sw 1
Rl sw 0 50
.end
"""
    hand = [GatedSwitch(name="S1", n_pos="sw", n_neg="0", duty=0.4,
                        phase=0.0, f_sw=100e3, g_on=100.0, g_off=1e-6,
                        gate_source="Vg")]
    direct = _direct_solve(clean, hand, 100e3, K)
    assert np.max(np.abs(routed.Xn - direct.Xn)) < 1e-9


def test_routed_buck_matches_direct_assembly():
    K = 15
    rt = route_netlist(BUCK)
    routed = solve_ltp(SwitchedHBNet(rt.clean_netlist, rt.switches), rt.f_sw, K)

    clean = """Vin vin 0 12
L1 sw out 100u
C1 out 0 47u
Rl out 0 5
.end
"""
    hand = [
        GatedSwitch(name="S1", n_pos="vin", n_neg="sw", duty=0.4, phase=0.0,
                    f_sw=50e3, g_on=1000.0, g_off=1e-6, gate_source="Vg"),
        GatedSwitch(name="S2", n_pos="sw", n_neg="0", duty=0.6, phase=0.4,
                    f_sw=50e3, g_on=1000.0, g_off=1e-6, gate_source="Vgb"),
    ]
    direct = _direct_solve(clean, hand, 50e3, K)
    assert np.max(np.abs(routed.Xn - direct.Xn)) < 1e-9


# ----------------------------------------------------------------------
# 4. Router refusals
# ----------------------------------------------------------------------

def test_router_refuses_state_dependent_gate():
    rt = route_netlist(STATE_DEPENDENT)
    assert not rt.ok
    assert rt.refused and "state-dependent" in rt.refused[0][1]
    with pytest.raises(RoutingError, match="state-dependent"):
        rt.require_ltp()


def test_router_refuses_non_pulse_gate():
    rt = route_netlist(SINE_GATE)
    assert not rt.ok
    assert rt.refused and "duty" in rt.refused[0][1]
    with pytest.raises(RoutingError):
        rt.require_ltp()


def test_router_distinguishes_unpinned_gate_from_state_dependent():
    """An unpinned control node (referenced only to ground) is 'not pinned by
    any source', not 'state-dependent'. Ground is trivially a power node, so
    that verdict must key on a NON-ground control node touching the power path.
    """
    unpinned = (
        "V1 in 0 10\nR1 in sw 1\nS1 sw 0 gx 0 SWMOD\nRl sw 0 50\n"
        "Vg g 0 PULSE(0 1 0 0 0 5u 10u)\n"
        ".model SWMOD SW(Ron=10m Roff=1Meg Vt=0.5)\n.end")
    rt = route_netlist(unpinned)
    assert not rt.ok
    reason = rt.refused[0][1]
    assert "not pinned by any independent source" in reason
    assert "state-dependent" not in reason


def test_buckboost_sync_routes_and_inverts():
    """The inverting buck-boost routes to two complementary LTP switches and
    its k = 0 output is negative (inverting)."""
    rt = route_netlist(dpspice.example_text("buckboost_sync.sp"))
    assert rt.ok and len(rt.switches) == 2
    res = dpspice.solve_hb(dpspice.example_text("buckboost_sync.sp"), K=25)
    assert res.converged
    assert res.summary["harmonics"]["out"][0]["re"] < 0.0


def test_dispatch_surfaces_refusal_as_dpspice_error():
    with pytest.raises(DpspiceError, match="state-dependent"):
        dpspice.solve_hb(STATE_DEPENDENT)


# ----------------------------------------------------------------------
# 5. Hybrid and warm start vs the unmodified Newton solver
# ----------------------------------------------------------------------

def _mixed_setup(K):
    rt = route_netlist(MIXED)
    net = SwitchedHBNet(rt.clean_netlist, rt.switches, sampled_gates=True)
    diodes = [Diode(net, d.n_pos, d.n_neg, ShockleyDiode(**d.params))
              for d in rt.diodes]
    return rt, net, diodes


def test_hybrid_equals_unmodified_newton_baseline():
    """solve_hybrid wraps the stock solve_newton; the fixed point must be
    identical to calling the unmodified solver directly on the same
    assembled system."""
    K = 12
    rt, net, diodes = _mixed_setup(K)
    hybrid = solve_hybrid(net, diodes, rt.f_sw, K)

    _, net2, diodes2 = _mixed_setup(K)
    base = hb.solve_newton(net2, diodes2, rt.f_sw, K=K, tol=1e-10)

    assert hybrid.converged and base.converged
    assert np.max(np.abs(hybrid.Xn - base.Xn)) < 1e-9


def test_warm_start_reaches_same_fixed_point():
    """The clamped LTP warm start changes the Newton path, not the answer."""
    K = 12
    rt, net, diodes = _mixed_setup(K)
    cold = solve_hybrid(net, diodes, rt.f_sw, K)

    _, net2, diodes2 = _mixed_setup(K)
    warm = solve_hybrid(net2, diodes2, rt.f_sw, K, use_warm_start=True)

    assert cold.converged and warm.converged
    scale = np.max(np.abs(cold.Xn))
    assert np.max(np.abs(warm.Xn - cold.Xn)) / scale < 1e-6


def test_warm_start_clamps_junction_voltage():
    """The pre-solve iterate never exposes the diode to more than the clamp."""
    K = 12
    rt, net, diodes = _mixed_setup(K)
    X0 = warm_start(net, diodes, rt.f_sw, K, vd_clamp=0.4)
    n = net.n
    from mna import stack_to_nodes
    Xn = stack_to_nodes(X0, K, n)
    N = aft.oversample_N(K)
    vt = aft.to_time(Xn, K, N)
    for d in diodes:
        va = vt[d.ia] if d.ia >= 0 else 0.0
        vb = vt[d.ib] if d.ib >= 0 else 0.0
        assert np.max(np.real(va - vb)) <= 0.4 + 1e-9


# ----------------------------------------------------------------------
# 6. Synchronous boost: k = 0 harmonic vs the CCM conversion ratio
# ----------------------------------------------------------------------

def test_boost_sync_dc_within_ripple_of_ccm_ratio():
    """Vin / (1 - d) = 30 V; the k = 0 harmonic must land within the
    reported peak-to-peak ripple band (conduction losses plus the O(1/K)
    truncation bias of a capacitor-clamped switch stay inside it)."""
    res = dpspice.solve_hb(dpspice.example_text("boost_sync.sp"))
    assert res.converged
    out = res.summary["nodes"]["V(out)"]
    k0 = res.summary["harmonics"]["out"][0]
    assert k0["k"] == 0
    assert abs(k0["amplitude"] - 30.0) < max(out["ripple"], 0.1)


def test_boost_sync_dc_bias_is_order_one_over_K():
    """The capacitor-clamped switch node carries an O(1/K) DC truncation bias.

    The shipped example parameters keep the K = 20 error inside the ripple band,
    so the within-ripple test above passes by construction. This asserts the
    mechanism instead, independent of those parameters: the k = 0 error must
    strictly decrease over K and halve as K doubles (first-order convergence).
    The reference is a Richardson limit built from the two finest grids, so no
    conduction-loss constant is hard-coded.
    """
    netl = dpspice.example_text("boost_sync.sp")

    def dc(K):
        return dpspice.solve_hb(netl, K=K).summary["harmonics"]["out"][0]["re"]

    Ks = [5, 10, 20, 40, 80]
    vals = [dc(K) for K in Ks]
    # Richardson: v(K) = v_inf - a / K, from the two finest grids.
    a = (vals[-1] - vals[-2]) / (1 / Ks[-1] - 1 / Ks[-2])
    v_inf = vals[-1] - a / Ks[-1]
    errs = [abs(v_inf - v) for v in vals[:4]]

    assert all(e_next < e for e, e_next in zip(errs, errs[1:])), errs
    for e, e_next in zip(errs, errs[1:]):          # K doubles -> error halves
        assert 1.6 < e / e_next < 2.4, (e, e_next)
    assert all(v < 30.0 for v in vals)             # biased low, never overshoots


def test_warm_start_falls_back_to_cold_on_stiff_diode():
    """A warm start that misses the basin must not return a worse answer.

    The clamped warm start diverges on the snubbered asynchronous boost (a stiff
    switched-diode node) where the cold continuation converges. solve_hybrid
    falls back to the cold path automatically, so enabling the option still
    yields the converged cold result.
    """
    rt = route_netlist(dpspice.example_text("boost_async.sp"))

    def build():
        net = SwitchedHBNet(rt.clean_netlist, rt.switches, sampled_gates=True)
        diodes = [Diode(net, d.n_pos, d.n_neg, ShockleyDiode(**d.params))
                  for d in rt.diodes]
        return net, diodes

    K = 20
    net, diodes = build()
    cold = solve_hybrid(net, diodes, rt.f_sw, K, use_warm_start=False)
    net2, diodes2 = build()
    warm = solve_hybrid(net2, diodes2, rt.f_sw, K, use_warm_start=True)

    assert cold.converged and warm.converged
    scale = np.max(np.abs(cold.Xn))
    assert np.max(np.abs(warm.Xn - cold.Xn)) / scale < 1e-6


# ----------------------------------------------------------------------
# 7. Closed-form O(1/K) bias correction (validation/bias_closed_form.md)
# ----------------------------------------------------------------------

BOOST_D06 = """* synchronous boost, d = 0.6: truncation-biased output node
Vin vin 0 12
L1 vin sw 100u
S1 sw 0 g 0 SWMOD
S2 sw out gb 0 SWMOD
C1 out 0 100u
Rl out 0 20
Vg  g  0 PULSE(0 1 0 0 0 12u 20u)
Vgb gb 0 PULSE(1 0 0 0 0 12u 20u)
.model SWMOD SW(Ron=20m Roff=1Meg Vt=0.5)
.tran 0 5m
.end
"""


def _out_dc(netl, K, corrected=False):
    res = dpspice.solve_hb(netl, K=K, bias_correction=corrected)
    assert res.converged
    return res.summary["harmonics"]["out"][0]["re"]


def test_bias_correction_removes_first_order_error():
    """The corrected DC must sit an order of magnitude closer to the K -> inf
    limit than the plain truncated solve. The reference is a Richardson limit
    from the two finest grids (no hard-coded constants); the correction
    predicts >= 90% of the true bias in the prototype sweeps, so <= 15%
    residual is a loose bound at K = 10 where the plain error is ~6%."""
    ref = 2 * _out_dc(BOOST_D06, 80) - _out_dc(BOOST_D06, 40)
    plain = _out_dc(BOOST_D06, 10)
    corr = _out_dc(BOOST_D06, 10, corrected=True)
    assert abs(corr - ref) < 0.15 * abs(plain - ref), (plain, corr, ref)


def test_bias_correction_buck_is_structural_zero():
    """Complementary switches sharing the switched node have cancelling
    gate-spectrum tails (the residual defect lands on the stiff source node
    and ground), so the correction must leave the buck unchanged to solver
    precision -- not merely be small."""
    plain = dpspice.solve_hb(BUCK, K=15)
    corr = dpspice.solve_hb(BUCK, K=15, bias_correction=True)
    v0 = plain.summary["harmonics"]["out"][0]["re"]
    v1 = corr.summary["harmonics"]["out"][0]["re"]
    assert abs(v1 - v0) < 1e-12 * max(1.0, abs(v0))
    assert corr.solver == "ltp+bias"


def test_bias_correction_default_off_and_labelled():
    """Off by default (plain LTP route, unchanged results); on, the solver
    label carries the correction so downstream tables are honest about it."""
    plain = dpspice.solve_hb(BOOST_D06, K=15)
    assert plain.solver == "ltp"
    corr = dpspice.solve_hb(BOOST_D06, K=15, bias_correction=True)
    assert corr.solver == "ltp+bias"
    assert corr.summary["harmonics"]["out"][0]["re"] > \
        plain.summary["harmonics"]["out"][0]["re"]   # boost is biased low


def test_bias_correction_refuses_unsupported_paths():
    """The derivation covers the gated-switch LTP path only: diodes route to
    hybrid NR-HB (different truncation defect) and pure linear netlists have
    no gate tail. Both must refuse loudly, not silently ignore the flag."""
    with pytest.raises(DpspiceError, match="hybrid"):
        dpspice.solve_hb(dpspice.example_text("boost_async.sp"),
                         K=15, bias_correction=True)
    with pytest.raises(DpspiceError, match="no gated switch"):
        dpspice.run("Vs in 0 SINE(0 1 50)\nR1 in out 1k\nC1 out 0 1u\n"
                    ".tran 0 100m\n.end", mode="hb", bias_correction=True)


# ----------------------------------------------------------------------
# 8. Richardson K-extrapolation (--richardson)
# ----------------------------------------------------------------------

def test_richardson_matches_two_solve_extrapolation():
    """The feature must return exactly 2*X_2K - X_K on the shared harmonics
    and the fine solve's coefficients above K, at result order 2K."""
    res = dpspice.solve_hb(BOOST_D06, K=10, richardson=True)
    coarse = dpspice.solve_hb(BOOST_D06, K=10)
    fine = dpspice.solve_hb(BOOST_D06, K=20)

    def row(r, k):
        return r.summary["harmonics"]["out"][k]

    assert res.solver == "ltp+richardson"
    for k in range(0, 11):
        want = 2 * row(fine, k)["re"] - row(coarse, k)["re"]
        assert abs(row(res, k)["re"] - want) < 1e-12 * max(1.0, abs(want))
        assert row(res, k)["extrapolated"] is True
    for k in range(11, 21):
        assert abs(row(res, k)["re"] - row(fine, k)["re"]) < 1e-15
        assert row(res, k)["extrapolated"] is False


def test_richardson_removes_first_order_error():
    """Same acceptance shape as the closed-form test: the extrapolated DC
    must land an order of magnitude closer to the K -> inf limit than the
    plain K = 10 solve (reference from the two finest grids)."""
    ref = 2 * _out_dc(BOOST_D06, 80) - _out_dc(BOOST_D06, 40)
    plain = _out_dc(BOOST_D06, 10)
    rich = dpspice.solve_hb(BOOST_D06, K=10, richardson=True)
    corr = rich.summary["harmonics"]["out"][0]["re"]
    assert abs(corr - ref) < 0.15 * abs(plain - ref), (plain, corr, ref)


def test_richardson_reports_both_solve_times():
    res = dpspice.solve_hb(BOOST_D06, K=10, richardson=True)
    d = next(d for d in res.decisions if d.field == "richardson")
    assert "K=10" in str(d.value) and "K=20" in str(d.value)
    assert "ms" in d.reason and d.reason.count("ms") == 2


def test_richardson_refuses_bias_correction_combination():
    """Both flags correct the same defect; combining must refuse loudly."""
    with pytest.raises(DpspiceError, match="double-counts"):
        dpspice.solve_hb(BOOST_D06, K=10, richardson=True,
                         bias_correction=True)
    with pytest.raises(DpspiceError, match="K-fragile|hybrid"):
        dpspice.solve_hb(dpspice.example_text("boost_async.sp"),
                         K=15, richardson=True)
