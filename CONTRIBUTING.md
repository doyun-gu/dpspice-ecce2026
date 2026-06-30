# Contributing to DPSpice

Thanks for looking at the engine. This document covers the install layout, the
testing contract, and the two invariants the test suite enforces.

## Install layout

The core is intentionally lean (numpy + scipy + simpleeval). Everything
user-facing is an opt-in extra:

```bash
pip install -e .            # library core: `import dpspice`, the Python API
pip install -e .[cli]       # the `dpspice` command-line interface
pip install -e .[mcp]       # the `dpspice-mcp` MCP server
pip install -e .[viz]       # matplotlib, for the notebooks
pip install -e .[dev]       # everything needed to run the test suite + CI
```

The cross-validation oracle uses **ngspice**, an external binary installed
separately (macOS: `brew install ngspice`, Debian/Ubuntu: `apt-get install
ngspice`). Tests that need it skip cleanly when it is absent.

### Reproducing the validated environment

For a bit-for-bit reproduction of the stack the paper numbers were validated
against, install with the pinned lock as a constraints file:

```bash
pip install -e .[dev] -c requirements.lock
```

`requirements.lock` pins the exact full dependency tree (numpy 2.5.0, scipy
1.18.0, simpleeval 1.0.7, and everything the CLI / MCP / test layers pull in)
captured on Python 3.13. Regenerate it only after a deliberate dependency bump;
the header in the file documents how.

## Running the tests

```bash
pip install -e .[dev]
pytest                      # golden + determinism + error + reproduce-consistency
dpspice suite --quick       # real engine vs ngspice across circuit families
```

## Invariant 1 — determinism

The solver is deterministic. The same netlist run repeatedly through the public
API returns **bit-identical** waveforms. No code path seeds an RNG, and the
default adaptive solver has no nondeterministic branch. `tests/test_determinism.py`
asserts an exact (epsilon = 0) match over repeated runs for RLC, rectifier, and
WPT circuits.

If you ever add a path that can only guarantee a small documented epsilon (for
example a randomised initial guess), give it its own test entry with that
epsilon spelled out. Do not loosen the existing bound.

## Invariant 2 — golden regression baseline

Every headline number the paper reports is frozen once in
`tests/golden_reference.json`, together with its tolerance and a pointer to the
paper artifact it backs. `tests/test_golden.py` recomputes each from a real
engine run and fails if it drifts outside `atol + rtol*|value|`. This is how a
refactor (or a future compiled backend) that silently shifts a published result
gets caught.

The capture code and the test share `tests/golden_cases.py`, so the frozen
values and the live values are always produced by identical code. **Never edit
a value in the JSON by hand.** When you intentionally change a number, re-freeze
it from a real run:

```bash
pytest tests/test_golden.py --update-golden
```

Machine-dependent timings are NOT frozen as wall-clock. The scaling test
asserts only the sublinear-in-duration trend (the IDP speedup mechanism), never
a millisecond count.

## Reporting a paper <-> code disagreement

If a reproduced number contradicts the paper text, **do not edit the code or the
fixture to match.** The discrepancy is the finding. Record it in
`PAPER_CODE_MISMATCHES.md` and raise it. See `REPRODUCIBILITY.md` for the full
artifact-to-command map and the discrepancies currently on record.

## Error-handling contract

User-facing failures (empty netlist, missing `.tran`, unparseable value,
unsupported device, HB non-convergence, missing ngspice, oversized circuit,
ambiguous carrier) must raise a clear `DpspiceError` that names the fix (the
flag, card, or env var to use), never a raw traceback. The CLI turns these into
a clean nonzero exit and the MCP server into an `{"error": ...}` payload.
`tests/test_errors.py` pins the intended type and message for each. New
failure modes should follow the same pattern and gain a test.
