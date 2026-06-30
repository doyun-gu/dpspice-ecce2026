# DPSpice notebooks

Five worked examples. Each runs end-to-end after only:

```bash
pip install dpspice[viz]
```

`[viz]` adds matplotlib; the notebooks reach their data through bundled package
resources (`dpspice.example_text` / `dpspice.example_path`), so they run from any
working directory with no extra files to download.

| Notebook | What it shows |
|---|---|
| `01_quickstart.ipynb` | load -> info -> run -> plot, via the `import dpspice` API |
| `02_envelope_vs_classical.ipynb` | IDP envelope vs classical TD: agreement + speedup-vs-duration |
| `03_validation_against_ngspice.ipynb` | cross-validation (auto ngspice, with bundled `.raw` fallback) |
| `04_nonlinear_rectifier.ipynb` | the harmonic-balance path; conduction angle vs smoothing cap |
| `05_ieee_network_scaling.ipynb` | the speedup mechanism on bundled cases (IEEE case files not redistributed) |

Notebook 03 prefers an automatically-driven **ngspice** run but falls back to the
bundled LTspice `.raw` when ngspice is not installed, so it runs either way.

## Rendered outputs

The committed notebooks already contain their executed outputs (printed values
and figures), so they read correctly on GitHub without running anything. Every
value is produced by a real solve at execution time; nothing is hard-coded.

Figures and summary tables share one house style from `dpspice.plotting`
(`use_style()`, the `PALETTE`, and `table()`), so the whole set is visually
consistent. You can reuse it in your own figures: `from dpspice.plotting import
use_style, PALETTE, table`.

## Re-executing them

To regenerate the outputs yourself you also need the notebook tooling:

```bash
pip install dpspice[viz] jupyter nbconvert ipykernel
jupyter nbconvert --to notebook --execute --inplace notebooks/*.ipynb
```

Solve *timings* are machine-dependent and will differ from the committed run;
the state counts, solver selection, accuracy (NRMSE / R²), and the speedup
*trend* are deterministic.
