# LTspice cross-check decks

Self-contained LTspice decks that mirror the packaged switched examples, for an
independent per-harmonic check of the dpspice HB path. The decks are provided
ready to run; they have **not** been executed here. Outputs (`.raw`, `.log`)
are intentionally absent until you run LTspice.

## Open this folder

```bash
open /Users/doyungu/Developer/dpspice-ecce2026/validation/ltspice
```

## Run each deck

Open each `.cir` in LTspice and run it (or use the batch flag):

```bash
/Applications/LTspice.app/Contents/MacOS/LTspice -b boost_sync_d05_ccm.cir
/Applications/LTspice.app/Contents/MacOS/LTspice -b buck_sync_d03.cir
/Applications/LTspice.app/Contents/MacOS/LTspice -b boost_async_nom.cir
/Applications/LTspice.app/Contents/MacOS/LTspice -b hybrid_mix.cir
```

Each run writes, next to the deck:

| File | Contents |
|---|---|
| `<stem>.raw` | full waveform data |
| `<stem>.log` | the `.four` Fourier table used by the comparison |

## Compare against dpspice

```bash
python validation/ltspice/compare.py
```

The script parses each `.log` `.four` table and lines it up against
`dpspice.solve_hb` on the identical circuit, per harmonic. A deck whose `.log`
is missing is skipped with a named banner, so the script runs cleanly before
LTspice has produced anything.

## What to expect

- `buck_sync_d03`: DC within a few tenths of a percent of `d*Vin = 3.6 V`, all
  harmonics in close agreement (the buck sync switch feeds an inductor, no DC
  truncation bias).
- `boost_sync_d05_ccm`: DC below the lossless `Vin/(1-d) = 24 V` by the O(1/K)
  truncation bias plus conduction drop. The dpspice DC should approach the
  LTspice DC as K grows; at finite K expect a small residual gap at the switched
  node. Quote agreement at the filtered output, and cross-check the trend by
  re-running `compare.py` after raising K in the case table.
- `boost_async_nom`: waveform NRMSE in the fixed-step reference band
  (~0.2-3%). The snubber is included in both, so the DC operating points should
  match; this is the case that most directly checks the snubbered hybrid result.
- `hybrid_mix`: DC and low harmonics in close agreement; the 50 Hz fundamental
  dominates.

Per-harmonic relative error should track the exploration's findings: tight on
components above the measurement floor, looser on harmonics buried in numerical
noise.
