# Changelog

All notable changes to DPSpice are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **CLI ergonomics**: `dpspice <netlist>` with no command now implies `run`
  (drag a file into the terminal after typing `dpspice ` and press Enter);
  a `<netlist>` argument that is not a file on disk falls back to the
  bundled examples by name (`buck_sync.sp`, `buck_sync`, or
  `examples/buck_sync.sp` — a local file always wins, and the substitution
  is announced on stderr); and a new `dpspice examples` command lists the
  bundled netlists, prints one, or copies one out to edit (`--copy`).
  `dpspice validate --ref` resolves the bundled `.raw` references the same
  way. This makes the README's "examples resolve from any working
  directory" claim true — previously `dpspice run examples/rlc.sp` only
  worked from a repository checkout.

- **Switched-linear pipeline** (`dpspice.switching`). Gate-driven switches
  (`Sxxx n+ n- nc+ nc- MODEL` with `.model SW(Ron= Roff= Vt= [Vh=])`) are
  now first-class netlist elements. A router classifies every element
  (linear, source, gated switch, diode, gate drive) and extracts each
  switch's `(duty, f_sw, phase)` triple from its independent `PULSE` gate
  source, folding inverted trains to the complement duty. State-dependent
  and non-`PULSE` gates are refused with an error naming the element and the
  reason.
- **Three switched analysis paths**: LTP harmonic balance (switches become
  constant Toeplitz blocks, periodic steady state in one linear solve),
  envelope-bank transient (fixed-step trapezoidal integration of the
  harmonic envelopes over the constant coupling matrices), and hybrid NR-HB
  (constant switch blocks assembled once outside the Newton loop, diode
  blocks refreshed per iteration, with an optional clamped LTP warm start).
  Hybrid and warm-started results match the unmodified Newton solver within
  tolerance, asserted in the test suite.
- **CLI**: `dpspice route <netlist>` renders the routing table (nonzero exit
  plus the refusal reason when a netlist is not LTP-routable);
  `dpspice run --analysis {hb,envelope} --K <n> [--horizon 2m] [--dt 2u]`
  runs the new paths with per-harmonic output tables for HB and `|X0|`,
  `|X1|` envelope export for the envelope mode. `--analysis` is an alias for
  `--mode`; `--horizon` defaults to the `.tran` window.
- **Python API**: `dpspice.route(netlist)` returning a `RoutingTable`,
  `dpspice.solve_hb(netlist, K=...)` and
  `dpspice.solve_envelope(netlist, K=..., horizon=..., dt=...)`, returning
  the same `Result` objects as `run`.
- **Examples**: `buck_sync.sp`, `boost_sync.sp`, `buckboost_sync.sp`,
  `src_bridge.sp` (LTP and envelope paths), `boost_async.sp` (bare),
  `boost_async_snubber.sp` (documented operating-point shift) and
  `hybrid_mix.sp` (hybrid NR-HB path).
- **Closed-form O(1/K) truncation-bias correction for the LTP path**
  (`--bias-correction`, `solve_ltp(..., bias_correction=True)`). The DC
  defect of the truncated switch spectrum is derived in closed form from the
  gate tail (exact finite Parseval form, no fitted constants), embedded at
  k = 0 through the switch incidence, and iterated to a self-consistent
  fixed point. Parameter-free; structural zero on the buck; results
  labelled `ltp+bias`. Derivation and validation in
  `validation/bias_closed_form.md`. Off by default; refused (loudly) on the
  hybrid and non-switched paths where the derivation does not apply. The
  fixed point is contractive only in a bounded regime — a stiff switched
  node at high duty (low R·C·f_sw) can sit outside it and the iteration
  diverges rather than degrades (VALIDATION_REPORT.md, ledger B1);
  non-convergence surfaces as `converged=False` plus a typed dispatch/CLI
  error naming the regime, never a silently returned corrected value
  (regression-tested).
- **Richardson K-extrapolation for the LTP path** (`--richardson`,
  `solve_ltp_richardson`). Solves at K and 2K and returns 2·X₂ₖ − Xₖ on the
  shared harmonics (fine-only harmonics unextrapolated and labelled in the
  per-harmonic table); both solve times reported in the run record.
  Mutually exclusive with `--bias-correction` (same defect, would
  double-count).
- **Newton continuation for the hybrid NR-HB path.** When plain Newton
  stalls (the recorded K-fragility on hard diode commutation),
  `solve_hybrid` engages geometric source stepping (10% → 100%,
  warm-started, adaptive step-back) and, if needed, SPICE-style Gmin
  stepping on the node-voltage diagonals. Cases plain Newton already solves
  are byte-identical (asserted); continuation rungs and iteration counts
  surface as a run `Decision`. Both asynchronous-boost variants now
  converge at every K ∈ {10…40} (previously snubbered K=20 only). The
  hybrid path remains labelled experimental.
- **External validation campaign** (`VALIDATION_REPORT.md`,
  `validation/`): LTspice cross-validation decks with settled tails and
  finite gate edges (Finding L1), an in-repo switched-DAE transient
  reference (`validation/td_boost_async.py`) that corrected the
  asynchronous-boost TD reference to 29.315 V bare / 31.094 V snubbered
  (Finding L2), and duty/frequency/K validity matrices re-run with the
  shipped bias correction.

## [1.0.5] - 2026-07-03

### Added

- **`dpspice run --envelope`** (and `with_envelopes=True` on the Python API's
  `run`). Exports the phasor-magnitude envelope |X(t)| the IDP solver already
  computes internally as an `envelopes` list (same `{name, t, v}` shape and
  decimation as `waveforms`) in the `--out` / `--json` payload. Off by
  default — the payload shape is unchanged unless the flag is set. IDP solver
  only: `td` and `hb` runs leave `envelopes` absent and append a warning
  rather than fabricating one. Motivation: envelope figures (e.g. the
  dpspice.com hero plot) are now reproducible from the public CLI instead of
  reaching into `dpspice._engine`.

### Changed

- **`dpspice reproduce` flags renumbered to the paper's final (v10) numbering.**
  Rectifier accuracy moved `--table 4` -> `--table 5` (Table V), the rectifier
  waveform moved `--figure 5` -> `--figure 6` (Fig. 6), and the external
  IEEE-network timing entry moved `--table 5` -> `--table 4` (Table IV). The
  computational benchmark stays `--table 3` (Table III). The stale external
  `--table 1` / `--table 2` stubs were removed: the RLC accuracy is paper
  Sec. III-A (with the offline IDP-vs-TD accuracy carried as Table II inside
  `--table 3`), and the coupled/WPT accuracy is Sec. III-E / Fig. 8 — reached
  via `dpspice validate` with your own `.raw`, not a numbered `reproduce` target.
  Values and golden references are unchanged; only the flag numbers and labels
  moved to track the camera-ready paper.

### Fixed

- **`dpspice reproduce --table 5` and `--figure 6` now run the paper's three
  rectifier loads, not one.** Both targets previously resolved every Table V row
  to the resistive half-wave deck (`rectifier_halfwave.sp`) and reported a single
  NRMSE, so the reproduced table looked as though it disagreed with the paper.
  Table V now solves each load at the harmonic count its conduction pulse needs
  (resistive K=15, RC mild 10uF K=30, RC strong 100uF K=40) and cross-checks each
  against its own bundled LTspice `.raw`. Figure 6 now draws the cap-smoothed
  `rectifier_rc.sp` waveform rather than the resistive deck. The regenerated
  NRMSE-vs-LTspice values are 0.409%, 0.134%, and 0.137%, matching the paper's
  reported 0.41%, 0.13%, and 0.14%. Two bundled references
  (`rectifier_rc.raw`, `rectifier_rc_mild.raw`) and one deck
  (`rectifier_rc_mild.sp`) now ship with the package, and three golden entries
  freeze the per-row NRMSE so the target cannot silently revert to a single load.

## [1.0.4] - 2026-07-01

Presentation release for the worked-example notebooks. No engine, solver, or
CLI-behavior change; every NRMSE, R², conduction angle, and speedup is exactly
what the engine produced in 1.0.3. This pass is about how those results read.

### Added

- **`dpspice.plotting.style_table(df, ...)`.** A small house-style helper that
  renders a pandas DataFrame as one consistent, clean table (teal header,
  right-aligned numerics, formatted values — no raw float spew). Replaces the
  ad-hoc matplotlib table previously in `dpspice.plotting`.
- **pandas** added to the `viz` (and `dev`) extra to back the styled tables.

### Changed

- **High-DPI figures.** `use_style()` now sets retina-friendly defaults
  (`figure.dpi` 150, `savefig.dpi` 200, 7×4 figsize) so notebook and README
  figures render crisp on screen and on GitHub.
- **Notebooks 01–05 re-rendered.** One styled table per result (duplicate
  plain-text dumps removed), paper-aligned titles, consistent palette, and a
  "corresponds to the paper" note in each header. Outputs re-committed; the
  numbers are unchanged.

[1.0.5]: https://github.com/doyun-gu/dpspice-ecce2026/releases/tag/v1.0.5
[1.0.4]: https://github.com/doyun-gu/dpspice-ecce2026/releases/tag/v1.0.4

## [1.0.3] - 2026-06-30

Documentation and presentation release. No engine, API, or CLI-behavior change
from 1.0.1; this version exists to make the citable archive internally
consistent (its code self-reports its own version) and to finish the docs around
the Zenodo DOI.

### Added

- **Zenodo DOI assets.** A DOI badge, a `doi:` field in `CITATION.cff`, and
  BibTeX entries (paper + software) in the README. Cite the concept DOI
  [`10.5281/zenodo.21085058`](https://doi.org/10.5281/zenodo.21085058), which
  always resolves to the latest archived version.
- **README user manual.** A copy-paste Quickstart (pipx install through first
  simulation, with captured output), a **Netlist format** section documenting
  the supported SPICE subset (R/L/C, V/I sources, K coupling, D diode) and what
  is not yet supported (MOSFET/BJT, subcircuits), and a top-of-README result
  figure (`docs/img/overview.png`) generated from a real engine run.

### Changed

- **Citation metadata corrected.** `CITATION.cff` now lists both authors
  (Gu, Doyun; Zhang, Cheng), the full paper title, and the Zenodo DOI.
- **Version strings unified.** `pyproject.toml`, `__version__`, the CLI banner,
  `CITATION.cff`, and the README all read 1.0.3 (the 1.0.2 archive shipped code
  that still self-reported 1.0.1; this release fixes that label mismatch).

[1.0.3]: https://github.com/doyun-gu/dpspice-ecce2026/releases/tag/v1.0.3

## [1.0.2] - 2026-06-30

Archival release. Re-tag of the 1.0.1 code to mint a citable Zenodo archive
under the concept DOI. No source change from 1.0.1 (the tagged tree still
self-reports 1.0.1; corrected in 1.0.3).

[1.0.2]: https://github.com/doyun-gu/dpspice-ecce2026/releases/tag/v1.0.2

## [1.0.1] - 2026-06-30

Cosmetic release. No behavior, API, or CLI-contract change; the 17 CLI-contract
tests and the golden baseline are unchanged from 1.0.0.

### Added

- **`dpspice --version`** (`-V`): prints the bare version string to stdout and
  exits, safe to capture in scripts.

### Changed

- **Interactive banner.** The `dpspice` startup banner is now an ANSI block-letter
  wordmark in a muted teal-green, with subtitle, version, and venue on a dim line
  below. It renders only on an interactive TTY and is still fully suppressed by
  `--quiet`, `--no-banner`, `--json`, or a piped/redirected stdout.
- **`dpspice validate` help text** clarifies that the positional argument is the
  netlist to simulate and `--ref` takes an LTspice/ngspice `.raw` *output* to
  validate against (not a netlist).

[1.0.1]: https://github.com/doyun-gu/dpspice-ecce2026/releases/tag/v1.0.1

## [1.0.0] - 2026-06-30

First public release: the reference implementation accompanying the ECCE 2026
paper. Topology-independent dynamic-phasor circuit simulation — netlist in,
result out, with the solver auto-decided, announced, and overridable.

### Added

- **Auto-deciding engine.** Three-tier dispatch: parse and build the MNA system
  (Tier 1), auto-estimate analysis mode / carrier frequency / harmonic count and
  announce each decision (Tier 2), with full overrides (Tier 3). Linear circuits
  solve with the instantaneous dynamic phasor (IDP) single-shift transient;
  circuits with a diode solve with harmonic balance (HB).
- **Stable Python API** (`import dpspice`): `load`, `info`, `run`, `validate`,
  `backend`, returning structured, serialisable result objects. Import-safe and
  side-effect-free.
- **CLI** (`dpspice`): `run`, `info`, `validate`, `bench`, `reproduce`, `suite`.
  Every command accepts `--json` (pure JSON on stdout) and `--quiet` (no banner,
  spinners, or Rich box chrome). Banners and spinners auto-disable off a TTY.
- **MCP server** (`dpspice-mcp`): tools `dpspice_info`, `dpspice_run`,
  `dpspice_waveforms`, `dpspice_validate`. Netlists pass as strings; results are
  bounded plain JSON. `dpspice_run` returns scalar summaries plus a per-node
  descriptor and a handle; arrays are fetched on demand, decimated, via
  `dpspice_waveforms`. All server logs are routed to stderr so the stdio channel
  stays clean.
- **Cross-validation** against ngspice and bundled LTspice `.raw` references,
  reporting NRMSE and R² per node.
- **Paper reproduction** (`dpspice reproduce`): regenerates Table 3 (computational
  benchmark), Table 4 (accuracy vs LTspice), and Figure 5 (rectifier waveform)
  from the real solver over bundled examples. Artifacts that depend on
  non-redistributed reference data are listed honestly rather than fabricated.
- **Bundled examples** shipped as package data and accessed via
  `importlib.resources`, so they resolve from an installed wheel regardless of
  the working directory: series RLC, half-wave rectifier, cap-smoothed rectifier
  (with LTspice `.raw` references where accuracy is claimed).
- **Determinism contract**: the same netlist returns bit-identical waveforms;
  the paper's headline numbers are frozen as a versioned golden baseline and
  re-checked on every run.
- **Documentation and reproducibility**: `README.md`, `CONTRIBUTING.md`,
  `REPRODUCIBILITY.md` (paper-artifact-to-command map), `PAPER_CODE_MISMATCHES.md`
  (honest scope notes), `CITATION.cff`, and a pinned `requirements.lock`.

### Notes

- Licensed under Apache 2.0 for its explicit patent grant (the IDP method has
  associated patent considerations).
- An HTTP service layer and a compiled backend are designed for but not shipped
  in this release; the pure-Python backend is the shipping path. See the README
  Roadmap.

[1.0.0]: https://github.com/doyun-gu/dpspice-ecce2026/releases/tag/v1.0.0
