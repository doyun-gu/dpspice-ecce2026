# LTspice cross-check decks

Self-contained LTspice decks that mirror the packaged switched examples, for an
independent per-harmonic and waveform check of the dpspice HB paths. Outputs
(`.raw`, `.log`) are untracked; run LTspice locally to regenerate them.

Deck conventions (established by the exploration campaign, restored here after
Finding L1 in the validation report): explicit 1 ns gate edges (a zero-slew
`PULSE` is silently limited to Ton/10, stretching the effective duty ~10%),
collection windows starting ≥10 settling time constants out, integer-period
`.four` windows, `plotwinsize=0`.

## Decks

| Deck | Path under test | Notes |
|---|---|---|
| `boost_sync_d05_ccm.cir` | LTP | d=0.5 CCM, settled 40 ms tail |
| `buck_sync_d03.cir` | LTP | no truncation bias at the output |
| `boost_async_nom.cir` | hybrid | snubber included, 40 ms tail |
| `boost_async_nosnub.cir` | hybrid | bare converter, pins the reference without the snubber's operating-point shift |
| `hybrid_mix.cir` | hybrid | 50 Hz fundamental |

## Run each deck (batch)

LTspice 26 for macOS is a CrossOver/Wine wrapper; the documented
`LTspice.app/Contents/MacOS/LTspice -b` invocation exits 0 without producing
output. Drive the bundled wine binary directly:

```bash
cd validation/ltspice
for d in boost_sync_d05_ccm buck_sync_d03 boost_async_nom boost_async_nosnub hybrid_mix; do
  /Applications/LTspice.app/Contents/SharedSupport/ltspice/bin/wine \
    --wait-children --bottle ltspice --workdir "$PWD" \
    "C:/Program Files/ADI/LTspice/LTspice.exe" \
    -b 'Y:\Developer\dpspice-ecce2026\validation\ltspice\'"$d"'.cir'
done
```

The `Y:` drive maps to the user home inside the LTspice bottle; adjust the
backslash path if the repo lives elsewhere.

(Or open each `.cir` in the LTspice GUI and run it.) Each run writes, next to
the deck:

| File | Contents |
|---|---|
| `<stem>.raw` | waveform data (LTspice 26 writes float64 time, float32 traces) |
| `<stem>.log` | the `.four` Fourier table used by the comparison |

## Compare against dpspice

```bash
python validation/ltspice/compare.py
```

Per deck, the script reports the `.four`-vs-HB harmonic table with a
data-driven measurement floor (spread of per-period means in the recorded
tail), and waveform NRMSE on the settled tail, full and AC-coupled. Decks
record from an integer number of switching periods, so the phase fold aligns
the two waveforms exactly. A deck whose output is missing is skipped with a
named banner.

## What to expect

As-run results and their interpretation live in the validation report, §2.
In brief:

- `buck_sync_d03` and `hybrid_mix`: everything inside the fixed-step band
  (~0.2–3%); harmonics agree to a few tenths of a percent.
- `boost_sync_d05_ccm`: finite-K O(1/K) truncation gap at DC *and* in the
  harmonic amplitudes; the Richardson K→∞ limit of the dpspice solve matches
  LTspice to 2 mV DC and sub-1% on the dominant harmonics.
- `boost_async_*`: hybrid-path finite-K DC bias (volts at K=20) over a ripple
  shape that agrees to a few percent AC-coupled. The settled LTspice DC
  references are 31.088 V (snubbered) and 29.310 V (bare), corroborated by
  the in-repo switched-DAE TD solver (`validation/td_boost_async.py`).
- The "full" NRMSE convention normalizes by reference peak-to-peak ripple, so
  a DC gap of volts over millivolt ripple reads as hundreds of percent; the
  AC-coupled figure isolates ripple-shape agreement.
