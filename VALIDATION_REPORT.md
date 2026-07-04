# Switched-linear pipeline validation report

Independent verification of the `feature/switched-linear` branch (integration
commit `f21fbb7`) against `main` (`22ab0ca`). The implementation was treated as
untrusted; the goal was to find where it breaks. Findings were recorded, then
fixed in separate commits, then the affected checks were re-run.

**Recommendation: GO for the gated-switch paths (LTP and envelope), conditional
GO for the hybrid diode path**, for merge to `main` after the camera-ready date.
The LTP and envelope paths are exact and robust across the full matrix. The
hybrid NR-HB (diode) path is correct where it converges and fails loudly where
it does not, but its convergence is fragile in the harmonic count; it is now
documented as such and should be labelled experimental. No paper-reproduction
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
truncation bias, confirmed independently in §7, now documented as a general
LTP-path property (Finding F2).

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

Decks are provided in `validation/ltspice/` and are **NOT run** (no `.raw`/`.log`
present). `compare.py` skips each case with a named banner until its LTspice
output exists.

| Deck | Purpose | Status |
|---|---|---|
| `boost_sync_d05_ccm.cir` | LTP path, d=0.5 CCM start-up | not run |
| `boost_async_nom.cir` | hybrid path, snubber included | not run |
| `buck_sync_d03.cir` | LTP path, no truncation bias | not run |
| `hybrid_mix.cir` | hybrid path, 50 Hz fundamental | not run |

Each deck matches the example's Ron/Roff/Vt exactly, sets `plotwinsize=0`, uses
≥200 points per switching period, and emits a `.four` table. Expected agreement
is waveform NRMSE ~0.2–3% and per-harmonic error consistent with the exploration
on components above the noise floor. `RUN.md` gives the folder-open command, the
batch run lines, and the comparison step.

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
  (loud failure). The **un-snubbered** circuit converges at more K (10,20,25,30)
  and tracks the TD reference far better (K=30 → 31.44 V vs snubbered 32.68 V).
  The snubber's real effect is a charge-injection operating-point shift, not a
  convergence prerequisite. The DC value does trend toward 31.38 V as K rises,
  but the Newton residual does not. Corrected in the README and the example
  comment (Finding F2).
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
| — | info | Lanczos-σ smoothing evaluated as an O(1/K) mitigation | Rejected (worsens DC and ripple); documented here, not shipped | — |
| — | pass | Rectifier solution byte-identical to main | none needed | — |

Supporting commits: tests `4f6f7d7`, LTspice assets `2e15899`, buck-boost
example `03b06d6`.

## Residual risk for the merge decision

The hybrid diode path is the only fragile surface. It is correct where it
converges (verified against routed/direct assembly and the un-snubbered TD
trend) and fails loudly otherwise, but a user cannot assume that raising `--K`
refines a hybrid diode result. Before merge, the maintainer should decide
whether to (a) label the hybrid path experimental in the public API, and (b)
re-tune or drop the snubbered `boost_async` example, whose shipped DC (32.9 V at
K=20) reflects the snubber's operating-point shift rather than the bare
converter. Neither blocks the gated-switch LTP/envelope paths, which are ready.
