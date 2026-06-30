# Reproducibility

This document maps every headline artifact in the ECCE 2026 paper to the exact
command that regenerates it and the value that command currently produces. All
numbers below come from real engine runs on this repository (Python backend,
numpy 2.5.0, ngspice 45.2 where a reference is auto-generated). Nothing is
hand-entered to match the paper. Where a regenerated number disagrees with the
paper text, it is recorded as a finding in the discrepancies section, not
silently changed.

For a bit-for-bit environment match, install with `-c requirements.lock` (see
`CONTRIBUTING.md`); the lock pins numpy 2.5.0, scipy 1.18.0, and the rest of the
tree captured on Python 3.13.

## What is and is not bundled

The repository ships everything needed to reproduce the **offline** artifacts:
the example netlists and the LTspice `.raw` references they validate against
(both as package data, accessed via `importlib.resources`). It does **not**
redistribute the paper's full external reference set — the LTspice Q-sweep
references (Table 1), the WPT link reference (Table 2), or the IEEE network case
files (Table 5). Artifacts that depend on those are marked "No" in the
reproducibility column below and are listed by `dpspice reproduce` with a note
on the external data they need, rather than shipped as fabricated numbers. To
reproduce them, point `dpspice validate --ref` at your own `.raw` / case files.

## How to reproduce

```bash
pip install -e .[dev]           # core + CLI + MCP + test suite
# ngspice is an external binary (not a pip extra): brew install ngspice
dpspice reproduce --table 3     # benchmark: state counts, solver choice, timings
dpspice reproduce --table 4     # rectifier accuracy vs bundled LTspice reference
dpspice reproduce --figure 5    # rectifier waveform samples
pytest -q                       # golden regression + determinism + error suite
```

The frozen regression baseline lives in `tests/golden_reference.json`; each
entry records its value, tolerance, and the paper artifact it backs. The
capture/recompute code is `tests/golden_cases.py`, shared by the test and the
re-freeze path (`pytest tests/test_golden.py --update-golden`).

## Artifact -> command -> expected value

| Paper artifact | Command | Expected (this repo) | Reproducible offline? |
|---|---|---|---|
| Table 3 — computational benchmark + per-duration IDP-vs-TD accuracy/speedup | `dpspice reproduce --table 3` | linear cases -> `td`/`idp`; rectifier cases -> `hb`, K=20, states=3. Plus `idp_vs_td_duration_sweep`: per-window NRMSE/R^2 and speedup (12/50/200 cycles). Timings machine-dependent (reported, not asserted); NRMSE/R^2 and speedup *trend* are frozen. | Yes |
| IEEE-network speedup envelope (23-57x / 224-566x at T=1s/10s) | (needs IEEE case files) | not redistributed. The bundled RLC duration sweep reproduces the same *trend* (~tenfold per decade); see discrepancy below. | No — bring IEEE cases; RLC sweep is the offline proxy |
| Table 4 — rectifier accuracy vs LTspice | `dpspice reproduce --table 4` | `worst_nrmse = 2.063e-3`, `min_r2 = 0.99997` against the bundled `examples/rectifier_halfwave.raw` | Yes (reference bundled) |
| Figure 5 — rectifier waveform | `dpspice reproduce --figure 5` | HB reconstructed V(out) samples (waveform JSON) | Yes |
| Rectifier accuracy at K=40 | `dpspice validate examples/rectifier_halfwave.sp --ref examples/rectifier_halfwave.raw --harmonics 40` | `worst_nrmse = 5.966e-4` | Yes (reference bundled) |
| Conduction angle vs smoothing cap | `dpspice run examples/rectifier_*.sp` (summary field) | half-wave 173.7 deg, C=10uF 100.5 deg, C=100uF 47.1 deg | Yes |
| IDP single-shift vs full TD (series RLC) | `dpspice run rlc.sp --mode idp` vs `--mode td` | NRMSE 6.5e-5, R^2 0.99999994 at 580 krad/s | Yes |
| Table 1 — RLC Q-sweep accuracy | `dpspice validate <your>.sp` | needs the original LTspice reference set (not redistributed) | No — bring your own `.raw` |
| Table 2 — WPT k=0.2 link accuracy | `dpspice validate <wpt>.sp` (auto ngspice) | vs ngspice: NRMSE 6.2e-5 (see discrepancy WPT below) | Partial — ngspice differs from paper's LTspice reference |
| Table 5 — IEEE-network timing | (needs IEEE case files) | not redistributed; validation suite has a steady-state smoke test | No |

## Determinism contract

The solver is deterministic. The same netlist run repeatedly through the
public API returns **bit-identical** waveforms (verified over 5 runs each for
RLC, rectifier, and WPT in `tests/test_determinism.py`, max abs diff
`0.000e+00`). No call path seeds an RNG; there is no nondeterministic path in
the default adaptive solver. This is why golden tolerances only need to absorb
cross-platform LAPACK/numpy float variation, not run-to-run noise.

## Known paper <-> code discrepancies (findings, NOT patched)

These regenerated numbers disagree with the paper text. They are recorded here
verbatim rather than adjusted, because the discrepancy itself is the finding
(it may mean the paper, the reference tool, or the comparison methodology needs
a footnote). See `PAPER_CODE_MISMATCHES.md` for the verifier-facing summary.

| Quantity | Paper text | This repo | Direction | Likely cause |
|---|---|---|---|---|
| WPT k=0.2 link accuracy (Table 2) | ~2.87% NRMSE vs LTspice | 6.2e-3 % (6.2e-5) vs ngspice | repo far tighter | Different reference tool (ngspice auto-run, fine step) than the paper's LTspice set; general SVD reduction now solves WPT cleanly |
| Coupled k=0.9 accuracy (Table 2) | ~0.54% NRMSE vs LTspice | 1.3e-4 % (1.3e-6) vs ngspice | repo far tighter | Same reference-tool / step difference |
| IDP single-shift vs full TD | < 1e-6 % | 6.5e-3 % (6.5e-5) | repo looser than paper | Paper likely reports a pointwise/relative error; repo reports NRMSE (RMSE / peak-to-peak) on an aligned grid |
| Rectifier accuracy at K=40 | 0.14% | 0.06% (5.97e-4) | repo tighter | Bundled LTspice reference + finer HB reconstruction |
| Conduction angles | 175 / 102 / 48 deg | 173.7 / 100.5 / 47.1 deg | **match** (within grid quantization 0.7 deg) | none — consistent |
| IDP-vs-TD accuracy vs window length | reported as a single "< 1e-6 %" | NRMSE *grows* with horizon: 6.5e-5 @12 cyc, 4.2e-4 @50, 1.9e-3 @200, 1.1e-2 @800, 4.4e-2 @3200 | repo shows horizon dependence | Long-horizon phase drift between the two solvers; the paper number is not horizon-flat (see PAPER_CODE_MISMATCHES.md finding 6) |
| Speedup envelope (IEEE, T=1s/10s) | 23-57x / 224-566x | reproduced as a *trend* on RLC: 1.9x@50cyc -> 7.3x@200cyc -> 112x@3200cyc (~tenfold/decade) | trend matches; absolute IEEE figures need external cases | IEEE case files not redistributed |

The accuracy-table discrepancies share one root: the paper compares against a
specific LTspice reference set that is **not redistributed** with this release,
while the offline-reproducible path compares against an auto-generated ngspice
run at a finer step. To reproduce the paper's exact percentages, point
`dpspice validate --ref` at the original LTspice `.raw` files. Until then, the
repository's vs-ngspice numbers are the authoritative, regenerable values and
are what the golden suite freezes.
