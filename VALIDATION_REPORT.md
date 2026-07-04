# Switched-linear pipeline validation report

Independent verification of the `feature/switched-linear` branch (integration
commit `f21fbb7`) against `main` (`22ab0ca`). The implementation was treated as
untrusted; the goal was to find where it breaks. Findings were recorded, then
fixed in separate commits, then the affected checks were re-run.

**Recommendation: GO for the gated-switch paths (LTP and envelope), conditional
GO for the hybrid diode path**, for merge to `main` after the camera-ready date.
The LTP and envelope paths are exact and robust across the full matrix. The
hybrid NR-HB (diode) path is correct where it converges and fails loudly where
it does not; plain Newton is fragile in the harmonic count, and a source/Gmin
continuation now closes every recorded stall (§7 table, 14/14 cells). The path
should still be labelled experimental until the continuation has seen a wider
circuit population. No paper-reproduction
path is touched (verified by hash, below).

## Preconditions

| Check | Result |
|---|---|
| Branch `feature/switched-linear` checked out | yes |
| Full suite green before changes | 84 passed |
| Full suite green after fixes | 88 passed |
| `git diff --stat main` on engine reproduction paths | only additive: `hb_solver.py` (+X0 kwarg, byte-identical default), `netlist_parser.py` (+`S` parse branch, unreachable for rectifier/RLC) |
| Rectifier solution byte-identical to main | yes, SHA256 `3048409b…086da19` on both (see §7) |

## 1. Netlist matrix

Every cell routed correctly and solved. Headline consistency is DC k=0 vs the
analytic averaged model, judged against the reported peak-to-peak ripple band.

### Duty sweep (f nominal 50 kHz, K=15, nominal load)

| Topology | d=0.2 | d=0.3 | d=0.5 | d=0.7 | d=0.8 |
|---|---|---|---|---|---|
| buck (err / ripple) | 0.0005 / 0.02 | 0.0007 / 0.03 | 0.001 / 0.03 | 0.002 / 0.03 | 0.002 / 0.02 |
| boost | 0.18 / 1.14 | 0.26 / 1.15 | 0.69 / 1.61 | **3.03 / 2.50** | **9.73 / 3.47** |
| buck-boost | 0.16 / 1.12 | 0.23 / 1.12 | 0.65 / 1.56 | **2.91 / 2.44** | **9.48 / 3.41** |

Buck: DC error is conduction loss only, inside ripple at every duty (the switch
feeds an inductor, no truncation bias). Boost and buck-boost: inside ripple
through d=0.5, **exceed ripple at d ≥ 0.7** (bold). This is the O(1/K) DC
truncation bias, confirmed independently in §7, documented as a general
LTP-path property (Finding F2) and since corrected in closed form
(`--bias-correction`, `validation/bias_closed_form.md`; re-run below).

### Duty sweep re-run with the shipped correction (`--bias-correction`, K=15)

| Topology | d=0.2 | d=0.3 | d=0.5 | d=0.7 | d=0.8 |
|---|---|---|---|---|---|
| boost (err / ripple) | 0.033 / 0.98 | 0.046 / 0.87 | 0.117 / 0.99 | 0.515 / 1.58 | 1.854 / 3.09 |
| buck-boost | 0.014 / 0.95 | 0.022 / 0.84 | 0.069 / 0.94 | 0.383 / 1.66 | 1.562 / 3.19 |

**Every previously out-of-band cell moves inside the ripple band**, including
d = 0.8 (boost 9.73 → 1.85 against a 3.1 V band; buck-boost 9.48 → 1.56
against 3.2 V). The residual against the averaged model is dominated by the
genuine Ron conduction drop — the averaged reference is lossless — plus the
O(1/K²) remainder of the correction (~0.38 V at d = 0.8,
`bias_closed_form.md` §5). The buck row is untouched by construction: the
complementary-pair correction is a structural zero there. The corrected
ripple figures differ slightly from the plain ones because the k = 0 defect
pushed through the full operator adjusts all retained harmonics.
The d = 0.8 regression is pinned parameter-independently
(`test_bias_correction_d08_improvement_beats_first_order_term`): the
improvement over the plain solve, measured against a Richardson reference,
must be at least the analytic first-order term evaluated at the plain
solution — every quantity from the solver, no hard-coded voltages.

### Frequency sweep (d=0.5, K=15): all cells route and solve; DC tracks the averaged model, ripple scales with period. nominal/5 widens ripple (boost 7.9 V), nominal×5 shrinks it (0.32 V); DC error is flat.

### K sweep (d=0.5): confirms first-order error decay

| K | boost err | buck-boost err | buck err |
|---|---|---|---|
| 7 | 1.27 | 1.22 | 0.001 |
| 15 | 0.69 | 0.65 | 0.001 |
| 25 | 0.47 | 0.42 | 0.001 |

### Light load (d=0.5, synchronous): buck 6.000 V, boost 23.39 V, buck-boost −11.40 V, all converged and inside ripple. Synchronous converters stay in forced CCM at light load (the sync FET carries reverse current), so the averaged model holds; only diode converters enter DCM (see §4).

### SRC full-bridge tank (Table I values): routes 4 switches, f_sw 91.76 kHz. Frequency sweep shows the correct resonant signature — V(n1) dips to 18.3 V at f0 (peak tank current, minimal tank impedance) and sits near the 63.7 V bridge fundamental off-resonance. K-stable (|V1(a)| = 63.6347 V at K=7,15,25, inductor-limited, no truncation drift).

### Shunt chopper on RLC tank: routes and solves at d=0.3,0.5,0.7 (no closed-form DC; envelope consistency checked).

### hybrid_mix (gated switch + diode + smoothing C): routes 1 switch + 1 diode, converges via hybrid NR-HB at K=20.

### Consistency checks

| Check | Result |
|---|---|
| envelope-bank t→∞ vs one-shot HB (buck d=0.5) | 1.2e-8 |
| envelope vs HB (boost d=0.5) | 1.5e-10 |
| envelope vs HB (buck-boost d=0.5) | 1.3e-6 at 40 ms (settling; 6.2e-8 at 160 ms) |
| envelope vs HB (SRC bridge, resonance) | 3.0e-11 |
| routed vs hand-assembled solve (boost) | 0.0e0 (bit-identical) |
| CLI vs Python API (boost, identical args) | 0.0e0, residuals match to 3.4e-13 |

## 2. External LTspice validation

All decks in `validation/ltspice/` have now been run (LTspice 26.0.2 for
macOS, batch mode; the working invocation is in `RUN.md` — the app is a
CrossOver/Wine wrapper and the documented `Contents/MacOS/LTspice -b` line
exits silently without output). A fifth deck, `boost_async_nosnub.cir`, was
added so the bare async converter is pinned independently of the snubber.

**Finding L1 (deck authoring, fixed before ingest):** the as-committed decks
drove the gates with zero-slew `PULSE` sources. LTspice silently limits
Tr=Tf=0 to Ton/10, which moves both Vt crossings and stretches the effective
duty by ~10% (measured: buck DC 4.009 V instead of 3.599 V, boost_sync
28.86 V instead of 23.9 V). Two decks also took their `.four` window before
the output had settled (boost_sync LC ring, spread 0.43 V across periods;
boost_async tail τ = 2RC = 4 ms, drift 0.25 V at the old 7.8 ms start). The
decks were re-authored with explicit 1 ns edges, collection windows ≥10 τ
out, and integer-period `.four` windows — the same conventions the
exploration campaign had already established. Solver untouched; this is
exactly the class of error the external check exists to catch, on either
side.

`compare.py` was extended for the ingest: a reader for LTspice 26's mixed
float64/float32 binary `.raw` layout (the packaged engine reader assumes
all-float64 and is deliberately left untouched), a data-driven measurement
floor (spread of per-period means inside the recorded tail), and waveform
NRMSE on the settled tail. The decks record from an integer number of
switching periods, so the fold onto the phase grid aligns LTspice and HB
exactly, with no fitted phase shift.

As-run results (`compare.py`, harmonic amplitudes from the `.four` tables):

| Deck | LTspice DC | dpspice DC (K) | harmonics k=1..5 | NRMSE full / AC-coupled |
|---|---|---|---|---|
| `buck_sync_d03` | 3.5992 V | 3.5993 V (15) | ≤0.2% | 0.45% / 0.02% |
| `hybrid_mix` | 8.7691 V | 8.7736 V (20) | ≤0.4% | 0.68% / 0.62% |
| `boost_sync_d05_ccm` | 23.897 V | 23.531 V (25) | see below | 344% / 156% |
| `boost_async_nom` | 31.088 V | 32.908 V (20) | 8–58% | 907% / 3.4% |
| `boost_async_nosnub` | 29.310 V | 32.431 V (20) | 19–77% | 1776% / 7.5% |

Buck and hybrid_mix land inside the expected fixed-step band (~0.2–3%) on
every metric: the paths without a truncation-biased node agree with LTspice
to a few tenths of a percent per harmonic. The three boost rows are outside
the band and are accounted for as follows.

- **The "full" NRMSE column is pp-normalized.** The repo's `nrmse()`
  convention divides by the reference peak-to-peak, which is millivolts of
  ripple here; a DC gap of volts then reads as hundreds of percent. The DC
  row and the AC-coupled column carry the information; the full column is
  kept for honesty about the convention.
- **boost_sync: finite-K truncation, vanishing in the limit.** The 0.366 V
  DC gap at K=25 is the §7 O(1/K) bias. It is not only DC: the harmonic
  amplitudes at the output carry the same O(1/K) error (fundamental 0.234 at
  K=25 vs LTspice 0.049). Richardson-extrapolating dpspice K∈{200,400}
  reproduces LTspice to 2 mV at DC (23.899 vs 23.897) and sub-1% on the
  dominant odd harmonics (k=1: 0.0490 vs 0.0490; k=3: 0.00539 vs 0.00539;
  k=5: 0.00195 vs 0.00194). The even harmonics are second-order small
  (≤3 mV absolute, ~1e-4 of DC) on both sides. The K→∞ agreement is
  external confirmation that the LTP path solves the right circuit and that
  the entire finite-K gap is the truncation tail — the target of the
  closed-form bias correction (`validation/bias_closed_form.md`).
- **boost_async (both variants): hybrid-path finite-K bias, plus a wrong
  reference in the earlier report — see Finding L2 in §7.** The K=20 DC gaps
  (1.8 V snubbered, 3.1 V un-snubbered) are the same truncation mechanism at
  the diode-clamped switch node. Ripple shape agrees to 3.4% / 7.5%
  (AC-coupled) despite the DC offset.

Measurement floors (per-period-mean spread of the recorded tail) are
2×10⁻⁵…10⁻³ V per deck; every harmonic quoted above is above its deck's
floor.

## 3. Router adversarial tests

All probes behaved correctly. No stack trace reached the caller; no silent
misroute.

| Probe | Result |
|---|---|
| PULSE nonzero tr/tf (within edge fraction) | routed, d=0.401, phase folds half-edges |
| PULSE delay td=2.5 µs | routed, phase=0.25 |
| duty from ton/period (ton=7 µs) | routed, d=0.7 |
| two switches sharing one gate | both route from the shared source |
| gate source pins nothing real (control `gx`) | refused: not pinned by any source (see R1) |
| dangling model reference | refused: no `.model NOPE SW(...)` card |
| model card wrong type (D not SW) | refused: no SW card |
| mixed-case element and node names | routed (case-insensitive) |
| `meg` vs `m` suffix | routed, g_on=200 (1/5m), g_off=5e-7 (1/2meg) |
| comment + continuation lines around S/.model | routed, continuation joined |
| control node to power path via 1e9 Ω, no source | refused: state-dependent |
| control node to power path via 1 Ω, no source | refused: state-dependent (identical verdict, value-independent) |
| `--analysis envelope` with no `.tran` | clean `DpspiceError`: needs a horizon |

The value-independent refusal is topological: a control node touching the power
path is refused whether the coupling resistor is 1 Ω or 1e9 Ω. Finding R1 (below)
corrected a misleading refusal *message* for an unpinned grounded gate; the
verdict was always correct.

## 4. Validity-boundary behaviour

- **DCM probe (async boost, light load).** The hybrid path tracks the rising
  DCM output as load lightens (Rl 20→400 Ω gives V(out) 24→38 V) and fails
  loudly with a clean `DpspiceError` at near-no-load (Rl=2000 Ω). Minimum
  acceptable outcome met: correct solve or documented loud failure, never a
  silent wrong answer. Documented in README limitations.
- **Hard-chopping accuracy vs K.** The O(1/K) DC bias lives at the *switched*
  node; accuracy must be quoted at the *filtered* node of interest. The README
  limitations now say this explicitly (Finding F2).
- **Warm-start robustness.** Clamped warm start across the diode cells:
  hybrid_mix converges in 22 iterations (vs 58 cold); **boost_async diverges**
  (converged=False, rel_diff 2.0) where cold converges. The unclamped warm start
  also diverges on hybrid_mix (residual 1e8), confirming the clamp is load-
  bearing. `use_warm_start` is **not exposed** through the CLI, `dispatch`, or
  the public `solve_hb`/`solve_envelope` API (verified by grep); it is reachable
  only via `dpspice.switching.solve_hybrid(use_warm_start=True)`. Finding F3 adds
  a cold-start fallback so even that entry point is now safe.

## 5. Performance regression

| Path | Time |
|---|---|
| RLC paper benchmark (non-switching) | 278 ms (byte-identical code path to main, no regression possible beyond a per-element branch check) |
| LTP boost_sync K=15 | 1.7 ms (single linear solve) |
| envelope boost_sync K=5 | 4.5 ms |
| hybrid boost_async K=20 | 56 ms (Newton) |

No envelope cell is slower than a switched time-domain reference at matched
accuracy — the envelope bank is milliseconds against any transient run.

## 6. Documentation audit

Every switched-circuit command in the README ran verbatim on the installed
package:

- `dpspice route examples/buck_sync.sp` — ok
- `dpspice run examples/buck_sync.sp --analysis hb --K 7` — ok
- `dpspice run examples/buck_sync.sp --analysis envelope --K 5 --horizon 2m --dt 2u` — ok
- `dpspice.route(netlist)`, `dpspice.solve_hb(netlist, K=7)`, `dpspice.solve_envelope(netlist, K=5, horizon="2m")` — ok

The CLI requires the `[cli]` extra (`pip install -e .[cli]`), which the README
documents. No emoji, meta-commentary, or filler in the switching module or the
new validation assets. The limitations section was rewritten to match the §4
findings (Finding F2).

## 7. Independent verification of the integration claims

- **O(1/K) DC truncation bias — CONFIRMED.** On the shipped `boost_sync` (d=0.6,
  Ron=20 mΩ, C=100 µF), the k=0 error over K∈{5,10,20,40,80,160} is
  {3.34, 1.78, 0.91, 0.46, 0.23, 0.12} against the Richardson limit 29.807 V.
  Log-log slope −0.972 (first order). Richardson limit is 0.19 V below the
  lossless 30 V, matching the conduction drop. The packaged within-ripple test
  passes by construction (parameters tuned so K=20 lands in the ripple band); a
  new parameter-independent test asserts the *mechanism* (strictly decreasing,
  halving as K doubles). **Lanczos-σ smoothing tested as a mitigation and
  rejected**: multiplying S_k by sinc(k/K) moves DC *further* from ideal at every
  K (K=20: 27.08 vs baseline 28.89) and roughly doubles ripple. Not shipped.
- **boost_async snubber and convergence — PARTIALLY REFUTED.** The claim
  "converges O(1/K) toward 31.38 V" does not hold as stated. The snubbered
  circuit converges only at K=20; K∈{15,25,30,35,40} stall at residual 1e-7…1e-8
  (loud failure). The **un-snubbered** circuit converges at more K (10,20,25,30).
  The snubber's real effect is a charge-injection operating-point shift, not a
  convergence prerequisite. Corrected in the README and the example comment
  (Finding F2).
- **The 31.38 V "un-snubbered TD reference" itself was wrong — Finding L2.**
  The number appears nowhere in the repo outside this report and fails a
  physics bound: a bare CCM boost cannot exceed the lossless
  Vin/(1−d) = 30 V, and the diode drop puts it below that. Two independent
  settled references now agree to ~6 mV: the in-repo switched-DAE TD solver
  (`validation/td_boost_async.py`, trapezoidal + backward-Euler edge steps,
  pnjlim Newton, drift <2 µV per 100 cycles) gives **29.315 V bare** and
  **31.094 V snubbered**; LTspice 26 (settled 40 ms tail) gives 29.310 V and
  31.088 V. The 31.38 V figure sits 0.29 V from the *snubbered* value and
  2.07 V above the bare one — evidently a snubbered and/or unsettled TD run
  quoted as un-snubbered. The hybrid solve's DC trend (32.43 V at K=20,
  31.44 V at K=30, un-snubbered) extrapolates first-order in 1/K to ≈29.5 V,
  i.e. toward the *correct* bare reference; the qualitative "trend toward
  the TD reference" conclusion survives, aimed at 29.32 V rather than
  31.38 V. The continuation-solver acceptance target is restated
  accordingly.
- **K-fragility of the hybrid path — CLOSED by Newton continuation (Task 2).**
  `solve_hybrid` now engages geometric source stepping (10% → 100%,
  warm-started, adaptive step-back) when plain Newton stalls, with a Gmin
  ladder behind it. Cases plain Newton already solves never enter the new
  code and are byte-identical (asserted in the suite). Full matrix, both
  variants of the asynchronous boost, `tol` default:

  | K | bare route | bare iters | bare DC (V) | snubbered route | snub iters | snub DC (V) |
  |---|---|---|---|---|---|---|
  | 10 | newton | 269 | 35.178 | newton | 269 | 35.247 |
  | 15 | continuation (8 rungs, from α=0.025) | 869 | 33.370 | continuation (5 rungs) | 569 | 33.572 |
  | 20 | newton | 269 | 32.431 | newton | 65 | 32.908 |
  | 25 | continuation (7 rungs, from α=0.05) | 749 | 31.839 | continuation (5 rungs) | 569 | 32.798 |
  | 30 | continuation (8 rungs) | 869 | 31.439 | continuation (5 rungs) | 569 | 32.684 |
  | 35 | continuation (8 rungs) | 869 | 31.077 | continuation (5 rungs) | 569 | 32.274 |
  | 40 | continuation (8 rungs) | 869 | 30.863 | continuation (7 rungs) | 383 | 31.999 |

  All 14 cells converge (previously: snubbered K=20 only, bare
  K∈{10,20,25,30}). Source stepping alone rescued every stall; the Gmin
  ladder never engaged. Iteration counts include the failed plain attempt
  (269 of them where it stalls) plus every continuation rung. The bare DC
  is monotone toward the settled 29.315 V reference and now sits cleanly
  in the 1/K regime: Richardson over the two finest grids gives 29.36 V,
  50 mV from the TD value. The snubbered DC is also monotone toward its
  31.094 V reference (0.91 V above at K=40) but is **not yet in the clean
  1/K regime** — pairwise Richardson estimates still disagree (32.1 V from
  K=25/30, 30.1 V from K=35/40), consistent with the snubber adding a fast
  time constant whose harmonics resolve later. Non-convergence, if the
  continuation is ever exhausted, still returns a loud error. The
  experimental label on the hybrid path stays.
- **Documentation framing — FIXED.** O(1/K) now reads as a general LTP-path
  property at stiff switched nodes, duty-dependent, quoted at the filtered node.
- **Warm-start X0 default — byte-identical to main.** Rectifier benchmark over
  {C=0, 1µF, 10µF} × {K=7,15}: identical SHA256 and identical iteration counts /
  residuals on both branches.

## Findings ledger

| ID | Severity | Finding | Fix | Commit |
|---|---|---|---|---|
| R1 | low | Unpinned gate referenced to ground was refused with a misleading "state-dependent" reason (ground is trivially a power node) | Key the state-dependent verdict on a non-ground control node; floating gate now reports "not pinned by any source". Verdict unchanged. | `399a300` |
| F3 | medium | Clamped warm start diverges on boost_async (converged=False); the exposed `solve_hybrid(use_warm_start=True)` could return a worse answer than default | Cold-start fallback when the warm Newton fails to converge; default path unchanged and byte-identical | `d5c82ab` |
| F2 | medium | README understated the O(1/K) bias (duty-dependent, exceeds ripple at d≥0.7) and mis-framed the boost_async snubber as a convergence fix | Reworked limitations and the example comment; added parameter-independent O(1/K) regression test | `03b06d6`, `4f6f7d7` |
| L1 | medium | LTspice decks drove gates with zero-slew PULSE sources (silently limited to Ton/10, ~10% duty stretch) and two decks sampled `.four` before settling | Re-authored all decks: 1 ns edges, ≥10 τ settling before the recorded tail, integer-period `.four`; results ingested in §2 | `b1be8cc` |
| L2 | medium | The reported 31.38 V un-snubbered boost_async TD reference exceeds the 30 V lossless bound; it matches a snubbered/unsettled run | Settled references pinned by two independent methods: 29.315 V bare / 31.094 V snubbered (in-repo TD, `validation/td_boost_async.py`) vs 29.310 / 31.088 V (LTspice). §7 and the Task 2 acceptance target corrected | `7284430` |
| — | info | Lanczos-σ smoothing evaluated as an O(1/K) mitigation | Rejected (worsens DC and ripple); documented here, not shipped | — |
| — | pass | Rectifier solution byte-identical to main | none needed | — |

Supporting commits: tests `4f6f7d7`, LTspice assets `2e15899`, buck-boost
example `03b06d6`.

## Residual risk for the merge decision

The hybrid diode path remains the least mature surface, but its two recorded
risks have narrowed since this section was first written. (a) The K-fragility
is closed operationally: the source/Gmin continuation converges every recorded
stall (14/14 cells in the §7 table) and byte-identical behaviour on
already-converging cases is asserted in the suite. What remains is that the
continuation is only as validated as that matrix — the experimental label on
the hybrid path stays until it has survived a wider circuit population. (b)
The snubbered example's DC question is resolved rather than re-tuned: the
32.9 V at K=20 is now known to be **mostly finite-K truncation bias, not the
snubber's operating-point shift** (the settled snubbered reference is
31.094 V, only 1.78 V above the bare 29.315 V; the remaining 1.8 V at K=20 is
bias that decays with K). The shipped `boost_async.sp` is now the bare
converter, with the snubbered variant kept as `boost_async_snubber.sp` and its
operating-point shift documented in the netlist header. A hybrid DC at
moderate K should still be quoted with a transient cross-check. None of this
blocks the gated-switch LTP/envelope paths, which are ready.
