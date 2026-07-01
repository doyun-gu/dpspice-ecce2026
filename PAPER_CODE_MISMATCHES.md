# Known paper <-> code differences

Policy for this repository: when a number reproduced by the released engine
disagrees with the paper text, the code and the golden fixture are **not**
edited to make them match. The discrepancy is documented here instead, so the
paper, the reference tool, or the comparison methodology can be corrected or
footnoted in a future revision. Nothing below has been patched.

All "this repo" numbers are real, captured once into `tests/golden_reference.json`
from the public adaptive API on 2026-06-30 (Python backend, ngspice 45.2).

## Findings

1. **WPT k=0.2 link accuracy — paper 2.87%, repo 0.0062% (vs ngspice).**
   The released engine includes a general SVD reduction (added after the paper
   experiments) that lets the WPT link solve through IDP cleanly; vs an auto
   ngspice reference the NRMSE is 6.2e-5. The paper's 2.87% is vs an LTspice
   reference set not redistributed here. Reconciling requires the original
   LTspice `.raw` to confirm whether 2.87% reproduces, or a footnote that the
   released engine is materially more accurate on this case.

2. **Coupled k=0.9 accuracy — paper 0.54%, repo 0.00013% (vs ngspice).**
   Same pattern and likely same root cause (reference tool + step). Requires
   the original LTspice reference to reconcile.

3. **IDP single-shift vs full TD — paper "< 1e-6 %", repo 0.0065%.**
   This is the one case where the repo is LOOSER than the paper claim. Likely a
   metric definition gap: the repo reports NRMSE (RMSE / peak-to-peak) on an
   aligned time grid, whereas "< 1e-6 %" reads like a pointwise or relative
   steady-state error. A revision should confirm which metric the paper
   sentence refers to and align the wording.

4. **Rectifier accuracy at K=40 — paper 0.14%, repo 0.06%.**
   Repo is tighter, against the bundled LTspice reference. Minor; likely a finer
   HB reconstruction or a different K/step in the paper run.

5. **Conduction angles — MATCH.** Paper 175/102/48 deg; repo 173.7/100.5/47.1
   deg, within the 0.7 deg phase-grid quantization. No action.

6. **IDP-vs-TD accuracy is horizon-dependent, not flat.** The paper states the
   IDP single-shift matches full TD to "< 1e-6 %", read as a single horizon-
   independent number. The reproducible duration sweep
   (`dpspice reproduce --table 3` -> `idp_vs_td_duration_sweep`) shows NRMSE
   GROWS monotonically with the simulated window: 6.5e-5 @12 cycles, 4.2e-4
   @50, 1.9e-3 @200, 1.1e-2 @800, 4.4e-2 @3200 (R^2 0.9999999 -> 0.984). The
   solvers diverge slowly over many carrier cycles (phase drift). These are the
   engine's actual outputs, not patched. A revision should either (a) state the
   horizon at which the accuracy figure was measured, or (b) report accuracy
   per duration as the sweep does. The short-horizon numbers are excellent; the
   issue is only the implied horizon-independence.

7. **IEEE speedup envelope not reproducible offline.** The paper's headline
   speedups (23-57x at T=1s, 224-566x at T=10s) are measured on IEEE networks
   whose case files are NOT redistributed with this release, so the exact
   figures cannot be regenerated here. The bundled RLC duration sweep
   reproduces the same TREND from real runs (1.9x @50 cycles, 7.3x @200, 112x
   @3200 — roughly tenfold per decade of duration), and `test_speedup_trend`
   freezes that trend. To reproduce the published IEEE numbers, add the IEEE
   case files (or cite a DOI for them) and re-run the timing benchmark.

## Resolution options

The four accuracy discrepancies all point the same way: the paper's
percentages come from an LTspice reference set that is not part of this
release, and the offline-reproducible path uses ngspice at a finer step. Two
clean resolutions:

- (a) Bundle (or cite a DOI for) the exact LTspice `.raw` files used in the
  paper, and reproduce the published percentages against them; or
- (b) Footnote the paper that the released engine is validated against ngspice
  and report the (tighter) repository numbers, treating the LTspice figures as
  the original-submission reference.

In both cases the code and the fixture stay as they are — the numbers above
are what the engine actually produces.
