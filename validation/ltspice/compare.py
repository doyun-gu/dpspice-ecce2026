"""Compare LTspice .four output against the dpspice HB harmonic table.

Run LTspice on each .cir in this folder first (see RUN.md); LTspice writes a
matching .log next to each deck containing the .four Fourier table. This script
parses those tables and lines them up against dpspice.solve_hb on the identical
circuit, per harmonic.

The decks are NOT run here. Any deck whose .log is missing is SKIPPED with a
named banner, so this script is safe to run before LTspice has produced output.
"""
from __future__ import annotations

import os
import re

import numpy as np

import dpspice

HERE = os.path.dirname(os.path.abspath(__file__))

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
    ("hybrid_mix", 50.0, "out", 20,
     dpspice.example_text("hybrid_mix.sp")),
]


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
    return dc, amps, r.converged


def main():
    bar = "=" * 68
    for stem, f0, node, K, netlist in CASES:
        log = os.path.join(HERE, stem + ".log")
        print(bar)
        print(f"CASE {stem}  (fundamental {f0:g} Hz, node V({node}), K={K})")
        if not os.path.exists(log):
            print(f"  SKIP: LTspice output {stem}.log not found in this folder.")
            print(f"        Run LTspice on {stem}.cir first (see RUN.md).")
            continue
        n_harm = 5
        lt_dc, lt_amps = parse_four_log(log, n_harm)
        dp_dc, dp_amps, conv = dpspice_harmonics(netlist, node, K, n_harm)
        print(f"  dpspice converged={conv}")
        print(f"  {'k':>3s} {'LTspice':>12s} {'dpspice':>12s} {'rel err':>10s}")
        for label, lt, dp in [("DC", lt_dc, dp_dc)] + [
                (str(k + 1), lt_amps[k], dp_amps[k]) for k in range(n_harm)]:
            if lt != lt and dp != dp:
                continue
            denom = max(abs(lt), 1e-12)
            rel = abs(lt - dp) / denom
            print(f"  {label:>3s} {lt:12.5g} {dp:12.5g} {rel:10.2%}")
    print(bar)


if __name__ == "__main__":
    main()
