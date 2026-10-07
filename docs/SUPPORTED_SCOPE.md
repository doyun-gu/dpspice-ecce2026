# DPSpice Supported Scope (v1.1.1)

v1.1.1 is a packaging and documentation patch. Simulation behaviour is the same as in v1.1.0.

## Supported

- **Linear RLC transient analysis**, for example `dpspice run rlc.sp --out rlc-result.json`.
- **Reproduction of the bundled diode-rectifier harmonic-balance reference results** (paper Table V) with `dpspice reproduce --table 5 --json`. This comparison uses stored LTspice reference data, so it does not need an LTspice installation.
- **Prescribed PULSE-gated switched-linear circuits** using harmonic-balance or envelope analysis, for example `buck_sync.sp` with `--analysis hb --K 7`. These results include finite-harmonic truncation error. The switched-linear model requires a prescribed gate pattern and continuous conduction; dead time, discontinuous conduction and state-dependent commutation are outside that model. See the [detailed limitations](../README.md#switched-circuits).
- **JSON waveform output** from the CLI.

## Experimental

- **Hybrid switch + diode path.** This path exists in the code but is experimental. It is not recommended for demonstrations or for results you depend on.

## Not included in this release

- Arbitrary SPICE vendor device models
- Feedback or closed-loop control
- Native import of KiCad, Altium, or LTspice schematics
- A general GPU or native-code backend
- Any certification or qualification claims
- Performance or speed guarantees beyond those already stated in the README
- Device or platform compatibility beyond what the README states

## Optional components (unchanged)

The optional MCP integration and the notebooks are unchanged from v1.1.0. This release does not newly promote them. MCP and notebook behaviour are not part of the conference demonstration promise; consult the release verification record for what was actually checked.

## Browser version

[dpspice.com](https://dpspice.com) runs a separately pinned public v1.1.0 engine through an adapter. Its inputs and features are not guaranteed to match the CLI.

## Verification status

- The project's existing golden-reference and determinism tests are unchanged.
- The release checks exercise installed wheel and source archives, waveform exports, Table V accuracy and unsupported-device refusal.
- Local preparation targets macOS Python 3.13. Cross-platform status must be taken from actual GitHub Actions runs for the release commit, not from this document.
- A passing example is not arbitrary-circuit compatibility, control stability or hardware qualification.
