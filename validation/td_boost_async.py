"""Switched-DAE time-domain reference for the boost_async example.

Pins the DC operating point of the packaged async boost, with and without
the snubber, independently of both the HB solver under test and LTspice:
trapezoidal integration inside gate segments, backward Euler on the
edge-crossing step (pure trapezoidal is unstable across a series-switch
turn-ON edge), per-step Newton on the diode with SPICE-style pnjlim
junction limiting (the bare switch node has no capacitance, so the plain
damped Newton of ``reference_td.simulate`` limit-cycles on the exponential).

Run from the repo root with the project venv:

    python validation/td_boost_async.py

Expected (settled to <2 uV/100 cycles drift; LTspice 26 agrees to ~6 mV):
    no snubber : V(out) DC = 29.315 V
    snubbered  : V(out) DC = 31.094 V
"""
import time

import numpy as np

import dpspice
from dpspice.switching import route_netlist, SwitchedHBNet
from dpspice import _engine  # noqa: F401  side-effect: engine modules on sys.path

from device import ShockleyDiode   # noqa: E402
from reference_td import Diode     # noqa: E402

SNUB = dpspice.example_text("boost_async.sp")
NOSNUB = SNUB.replace("Csn sw 0 150n\n", "")


def td_reference(netlist, n_cycles=2500, spc=800, keep=2):
    """Return (t, x, out_index, per_cycle_means) for the settled tail."""
    rt = route_netlist(netlist)
    swnet = SwitchedHBNet(rt.clean_netlist, rt.switches, sampled_gates=True)
    n = swnet.n
    s = rt.switches[0]
    d0 = rt.diodes[0]
    law = ShockleyDiode(**d0.params) if d0.params else ShockleyDiode()
    diode = Diode(swnet, d0.n_pos, d0.n_neg, law)
    P_sw = swnet._P[0].real
    A = swnet.A.real.copy()
    E = swnet.E.real.copy()
    dt = 1.0 / (s.f_sw * spc)
    N = n_cycles * spc
    bfun = lambda t: swnet.mna.b_func(t).copy().real
    gfun = lambda t: (s.g_on if np.mod(t * s.f_sw - s.phase, 1.0) < s.duty
                      else s.g_off)
    vt = law.n * law.VT
    vcrit = vt * np.log(vt / (np.sqrt(2.0) * law.Is))
    x = np.zeros(n)
    t = 0.0
    b0 = bfun(t)
    f0v = diode.f_nl(x)
    nonconv = 0
    kbuf = keep * spc
    buf = np.zeros((kbuf, n))
    tb = np.zeros(kbuf)
    g_prev = gfun(0.25 * dt)
    iout = swnet.idx("out")
    cyc_means = []
    acc = 0.0
    for i in range(N):
        t1 = t + dt
        b1 = bfun(t1)
        g_new = gfun(t1 - 0.25 * dt)      # topology at the right end of the step
        A1 = A + g_new * P_sw
        if g_new != g_prev:               # edge step: BE damps the algebraic jump
            const = E @ x + dt * b1
            ci = dt
        else:                             # interior step: pure trapezoidal
            const = E @ x + 0.5 * dt * (A1 @ x + b0 + f0v + b1)
            ci = 0.5 * dt
        xx = x.copy()
        for _ in range(200):
            R = E @ xx - ci * (A1 @ xx + diode.f_nl(xx)) - const
            J = E - ci * (A1 + diode.jac_nl(xx))
            dxs = np.linalg.solve(J, -R)
            # pnjlim: cap the forward-bias overshoot of the junction voltage
            vold = diode.v_across(xx)
            vnew = vold + (dxs[diode.ia] if diode.ia >= 0 else 0.0) \
                        - (dxs[diode.ib] if diode.ib >= 0 else 0.0)
            dv = vnew - vold
            if vnew > vcrit and abs(dv) > 2.0 * vt:
                if vold > 0:
                    arg = 1.0 + dv / vt
                    vlim = vold + vt * np.log(arg) if arg > 0 else vcrit
                else:
                    vlim = vt * np.log(max(vnew, vt) / vt)
                dxs = dxs * ((vlim - vold) / dv)
            xx = xx + dxs
            if np.abs(dxs).max() < 1e-9:
                break
        else:
            nonconv += 1
        x = xx
        f0v = diode.f_nl(x)
        b0, t, g_prev = b1, t1, g_new
        acc += x[iout]
        if (i + 1) % spc == 0:
            cyc_means.append(acc / spc)
            acc = 0.0
        if i >= N - kbuf:
            j = i - (N - kbuf)
            buf[j] = x
            tb[j] = t1
    assert nonconv == 0, f"{nonconv} TD steps failed to converge"
    return tb, buf, iout, np.array(cyc_means)


if __name__ == "__main__":
    for name, net in [("no snubber", NOSNUB), ("snubbered", SNUB)]:
        t0 = time.perf_counter()
        tb, xb, iout, cm = td_reference(net)
        dc = xb[:, iout].mean()
        drift = cm[-100:].max() - cm[-100:].min()
        print(f"{name:11s}: V(out) DC = {dc:.4f} V over the last 2 cycles "
              f"(drift {drift:.1e} V/100 cycles, {time.perf_counter()-t0:.0f} s)")
