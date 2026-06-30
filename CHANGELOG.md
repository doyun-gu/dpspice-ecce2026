# Changelog

All notable changes to DPSpice are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
