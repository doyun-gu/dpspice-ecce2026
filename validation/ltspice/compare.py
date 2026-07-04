"""Compare LTspice output against the dpspice HB solution, per deck.

Run LTspice on each .cir in this folder first (see RUN.md); LTspice writes a
matching .log (the .four Fourier table) and .raw (waveform) next to each deck.
This script lines both up against dpspice.solve_hb on the identical circuit:

* per-harmonic table from the .four .log, with a data-driven measurement
  floor (the spread of per-period means inside the recorded window -- any
  component below it is quoted as below-floor, not as an error);
* waveform NRMSE on the recorded settled tail, full and AC-coupled. The
  decks record from an integer number of switching periods, so the fold onto
  the phase grid aligns the two waveforms exactly (no fitted phase shift).

The decks are NOT run here. Any deck whose output is missing is SKIPPED with
a named banner, so this script is safe to run before LTspice has produced
output.
"""
from __future__ import annotations

import os
import re

import numpy as np

import dpspice
from dpspice import _engine  # noqa: F401  side-effect: engine modules on sys.path

import metrics as M  # noqa: E402  rectifier/metrics.py
from ltspice_io import read_raw as _read_raw_f64  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def _nosnub(text):
    return text.replace("Csn sw 0 150n\n", "")


# Each case: deck stem, fundamental Hz, probe node, K, and the dpspice netlist
# for the SAME circuit the deck describes (duty/values matched to the .cir).
CASES = [
    ("boost_sync_d05_ccm", 50e3, "out", 25,
     """* boost d=0.5
Vin vin 0 12
L1 vin sw 100u
S1 sw 0 g 0 SWMOD
S2 sw out gb 0 SWMOD
C1 out 0 100u
Rl out 0 20
Vg  g  0 PULSE(0 1 0 0 0 10u 20u)
Vgb gb 0 PULSE(1 0 0 0 0 10u 20u)
.model SWMOD SW(Ron=20m Roff=1Meg Vt=0.5)
.tran 0 5m
.end"""),
    ("buck_sync_d03", 50e3, "out", 15,
     """* buck d=0.3
Vin vin 0 12
S1 vin sw g 0 SWMOD
S2 sw 0 gb 0 SWMOD
L1 sw out 100u
C1 out 0 47u
Rl out 0 5
Vg  g  0 PULSE(0 1 0 0 0 6u 20u)
Vgb gb 0 PULSE(1 0 0 0 0 6u 20u)
.model SWMOD SW(Ron=1m Roff=1Meg Vt=0.5)
.tran 0 2m
.end"""),
    ("boost_async_nom", 50e3, "out", 20,
     dpspice.example_text("boost_async.sp")),
    ("boost_async_nosnub", 50e3, "out", 20,
     _nosnub(dpspice.example_text("boost_async.sp"))),
    ("hybrid_mix", 50.0, "out", 20,
     dpspice.example_text("hybrid_mix.sp")),
]


def read_raw(path):
    """LTspice binary .raw: all-float64 (older format), falling back to the
    mixed layout LTspice 24+ writes (float64 time column, float32 traces)."""
    try:
        return _read_raw_f64(path)
    except ValueError:
        pass
    with open(path, "rb") as f:
        raw = f.read()
    enc = None
    for e in ("utf-16-le", "latin-1"):
        m = "Binary:\n".encode(e)
        i = raw.find(m)
        if i >= 0:
            enc, marker, hdr_end = e, m, i
            break
    if enc is None:
        raise ValueError(f"not a binary LTspice raw: {path}")
    header = raw[:hdr_end].decode(enc, errors="ignore")
    n_vars = n_pts = None
    names = []
    in_vars = False
    for line in header.splitlines():
        s = line.strip()
        if s.startswith("No. Variables:"):
            n_vars = int(s.split(":")[1])
        elif s.startswith("No. Points:"):
            n_pts = int(s.split(":")[1])
        elif s.startswith("Variables:"):
            in_vars = True
        elif in_vars:
            parts = s.split()
            if len(parts) >= 2 and parts[0].isdigit():
                names.append(parts[1].lower())
            if len(names) == n_vars:
                in_vars = False
    body = raw[hdr_end + len(marker):]
    rec = np.dtype([("t", "<f8"), ("v", "<f4", (n_vars - 1,))])
    arr = np.frombuffer(body, dtype=rec, count=n_pts)
    data = np.empty((n_pts, n_vars))
    data[:, 0] = np.abs(arr["t"])
    data[:, 1:] = arr["v"]
    return names, data


def trace(names, data, name):
    return data[:, names.index(name.lower())]


def parse_four_log(path, n_harm):
    """Return (dc, [amp_1..amp_n]) from an LTspice .four Fourier table."""
    text = open(path, "r", errors="ignore").read()
    dc = re.search(r"DC component[:=]\s*([-\d.eE+]+)", text)
    dc_val = float(dc.group(1)) if dc else float("nan")
    amps = {}
    for m in re.finditer(
            r"^\s*(\d+)\s+[-\d.eE+]+\s+([-\d.eE+]+)", text, re.MULTILINE):
        amps[int(m.group(1))] = float(m.group(2))
    return dc_val, [amps.get(k, float("nan")) for k in range(1, n_harm + 1)]


def dpspice_harmonics(netlist, node, K, n_harm):
    r = dpspice.solve_hb(netlist, K=K)
    h = r.summary["harmonics"][node]
    dc = h[0]["re"]
    # single-sided amplitude of harmonic k is 2*|X_k| for k>=1
    amps = [2.0 * np.hypot(h[k]["re"], h[k]["im"]) for k in range(1, n_harm + 1)]
    return dc, amps, r, h


def measurement_floor(t, v, f0):
    """Spread of per-period means inside the recorded window: residual
    settling drift + adaptive-step noise. Harmonic components below this are
    not measurable from the record."""
    T0 = 1.0 / f0
    n_per = int(round((t[-1] - t[0]) / T0))
    means = []
    for i in range(n_per):
        m = (t >= t[0] + i * T0) & (t < t[0] + (i + 1) * T0)
        if m.sum() > 2:
            means.append(np.trapezoid(v[m], t[m]) / (t[m][-1] - t[m][0]))
    means = np.asarray(means)
    return float(means.max() - means.min()) if means.size > 1 else 0.0


def waveform_nrmse(t, v, harm_rows, f0, grid=2000):
    """Fold the recorded LTspice tail onto one period and compare with the HB
    reconstruction on the same phase grid. The decks start recording at an
    integer number of periods after t=0, so the fold aligns exactly with the
    HB phase origin. Returns (nrmse_full, nrmse_ac): full includes the DC
    gap, AC subtracts each side's mean (ripple-shape agreement only)."""
    ph = np.linspace(0.0, 1.0, grid, endpoint=False)
    ph_r, v_r = M.fold_to_period(t, v, f0)
    v_ref = M.resample(ph_r, v_r, ph)
    v_hb = np.zeros(grid)
    for row in harm_rows:
        c = row["re"] + 1j * row["im"]
        k = row["k"]
        if k == 0:
            v_hb += c.real
        else:
            v_hb += 2.0 * np.real(c * np.exp(2j * np.pi * k * ph))
    full = M.nrmse(v_ref, v_hb)
    ac = M.nrmse(v_ref - v_ref.mean(), v_hb - v_hb.mean())
    return float(full), float(ac)


def main():
    bar = "=" * 68
    for stem, f0, node, K, netlist in CASES:
        log = os.path.join(HERE, stem + ".log")
        rawf = os.path.join(HERE, stem + ".raw")
        print(bar)
        print(f"CASE {stem}  (fundamental {f0:g} Hz, node V({node}), K={K})")
        if not os.path.exists(log):
            print(f"  SKIP: LTspice output {stem}.log not found in this folder.")
            print(f"        Run LTspice on {stem}.cir first (see RUN.md).")
            continue
        n_harm = 5
        lt_dc, lt_amps = parse_four_log(log, n_harm)
        dp_dc, dp_amps, result, harm_rows = dpspice_harmonics(
            netlist, node, K, n_harm)
        print(f"  dpspice converged={result.converged}")

        floor = None
        if os.path.exists(rawf):
            names, data = read_raw(rawf)
            t = trace(names, data, "time")
            v = trace(names, data, f"V({node})")
            floor = measurement_floor(t, v, f0)
            print(f"  measurement floor (per-period-mean spread in the "
                  f"recorded tail): {floor:.2e} V")

        print(f"  {'k':>3s} {'LTspice':>12s} {'dpspice':>12s} {'rel err':>10s}")
        for label, lt, dp in [("DC", lt_dc, dp_dc)] + [
                (str(k + 1), lt_amps[k], dp_amps[k]) for k in range(n_harm)]:
            if lt != lt and dp != dp:
                continue
            denom = max(abs(lt), 1e-12)
            rel = abs(lt - dp) / denom
            note = ""
            if floor is not None and label != "DC" and abs(lt) < floor:
                note = "  (below floor)"
            print(f"  {label:>3s} {lt:12.5g} {dp:12.5g} {rel:10.2%}{note}")

        if os.path.exists(rawf):
            full, ac = waveform_nrmse(t, v, harm_rows, f0)
            print(f"  waveform NRMSE vs recorded tail: full {full:.2%}  "
                  f"AC-coupled {ac:.2%}")
        else:
            print(f"  ({stem}.raw not found: waveform NRMSE skipped)")
    print(bar)


if __name__ == "__main__":
    main()
