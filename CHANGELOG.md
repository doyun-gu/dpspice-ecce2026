# Changelog

All notable changes to DPSpice are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
