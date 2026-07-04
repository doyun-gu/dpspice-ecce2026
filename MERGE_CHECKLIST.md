# Merge checklist — `feature/switched-linear`

Audit of the branch ahead of the merge decision. **Prepared, not executed:**
nothing here merges, tags, pushes, or releases. Evidence was produced on
2026-07-04 at evidence head `89c55bb` against `main` at `22ab0ca`
(18 commits ahead, 0 behind; the commit adding this file sits on top and
touches nothing else).

## 1. Diff scope — protected files untouched ✅

`git diff --stat main..HEAD`: **34 files changed, 3639 insertions(+),
20 deletions(-)**. Grep of the changed-file list for
`citation|zenodo|reproduc|golden|notebook|paper` matches nothing:

- `CITATION.cff` — untouched (no `.zenodo.json` exists in the repo)
- `tests/golden_reference.json`, `tests/golden_cases.py` — untouched
- paper-reproduction surfaces (`notebooks/`, `REPRODUCIBILITY.md`,
  `dpspice reproduce` fixtures) — untouched

Full changed-file set (`git diff --name-status main..HEAD`):

| Status | Files |
|---|---|
| Modified | `CHANGELOG.md`, `README.md`, `src/dpspice/__init__.py`, `src/dpspice/_engine/netlist_parser.py`, `src/dpspice/_engine/rectifier/hb_solver.py`, `src/dpspice/api.py`, `src/dpspice/cli.py`, `src/dpspice/dispatch.py`, `tests/test_cli_contract.py` |
| Added | `VALIDATION_REPORT.md`, `src/dpspice/switching/{__init__,envelope,gate,hybrid,ltp,router}.py`, `src/dpspice/data/examples/{boost_async,boost_async_snubber,boost_sync,buck_sync,buckboost_sync,hybrid_mix,src_bridge}.sp`, `tests/test_switching.py`, `validation/bias_closed_form.md`, `validation/td_boost_async.py`, `validation/ltspice/{.gitignore,RUN.md,compare.py,boost_async_nom.cir,boost_async_nosnub.cir,boost_sync_d05_ccm.cir,buck_sync_d03.cir,hybrid_mix.cir}` |

The engine-file edits (`netlist_parser.py`, `hb_solver.py`) are additive
(S-element parsing, optional Newton `X0`); item 3 proves the pre-existing
rectifier path is numerically byte-identical.

`exploration/basis-selection/` is deliberately **not** on the branch
(untracked; see item 7).

## 2. Full suite on a clean install ✅

Fresh venv (Python 3.13), `pip install '<repo>[mcp]' pytest`, then
`pytest tests/` from the repo root:

```
101 passed in 74.44s
```

The dev venv gives the same count (101 passed, ~74 s). The suite grew by one
test on this branch's final commits
(`test_bias_correction_divergence_is_loud_error`, ledger B1).

Note (pre-existing on `main`, not introduced here): installing **without**
the `[mcp]` extra makes pytest collection die with `INTERNALERROR:
SystemExit` because `tests/test_mcp_payload.py` imports
`dpspice.mcp_server`, which raises `SystemExit` when `mcp` is missing. The
extra is required to run the suite; recorded here so the failure mode is not
rediscovered.

## 3. Rectifier golden — byte-identical to main ✅

Method: half-wave rectifier benchmark over {C = 0, 1 µF, 10 µF} × {K = 7, 15}
(the VALIDATION_REPORT campaign grid), hashing the float64 bytes of every
exported waveform (`t` and `v`, name-sorted) plus iteration counts and
residuals (17 significant digits). Same script, same fresh venv, package
imported via `PYTHONPATH` from the branch checkout and from a `git worktree`
of `main`:

```
branch (89c55bb): b44767145e2f080f88585218daa5b0781d64b696643e53b379b95f2badb3a26c
main   (22ab0ca): b44767145e2f080f88585218daa5b0781d64b696643e53b379b95f2badb3a26c
```

Identical. `tests/golden_reference.json` is also untouched by the diff
(item 1), and the golden regression tests pass in both venvs.

## 4. Hybrid path labelled experimental ✅ (fixed during this audit)

The label previously existed only in CHANGELOG and VALIDATION_REPORT prose;
commit `89c55bb` added it at the user-facing surfaces. Verified by grep:

- `README.md:207` — "**Hybrid NR-HB** (`--analysis hb`, switches plus diodes) — **experimental**." (+ pointer to limitations and VALIDATION_REPORT)
- `src/dpspice/cli.py:194` — `--analysis` help: "The hybrid switch+diode path is experimental; cross-check its DC against a transient reference."
- `src/dpspice/switching/hybrid.py:205` — `solve_hybrid` docstring: "EXPERIMENTAL: this path is validated on a narrower circuit population…"
- `CHANGELOG.md:70` — "The hybrid path remains labelled experimental."
- `VALIDATION_REPORT.md` — residual-risk section, multiple mentions

## 5. CHANGELOG `[Unreleased]` consistent with the branch diff ✅

Every user-facing change in the diff maps to an `Added` bullet: switching
pipeline + router, three analysis paths, CLI (`route`, `--analysis`,
`--K`), Python API (`route`/`solve_hb`/`solve_envelope`), seven examples,
bias correction (now including the ledger-B1 contraction-regime note),
Richardson extrapolation, hybrid Newton continuation, validation campaign.
No `Changed`/`Removed` entries are owed: the only edits to pre-existing
files are additive plumbing, and item 3 shows the pre-existing rectifier
behaviour is bit-for-bit unchanged. No dependency or `pyproject.toml`
changes on the branch.

## 6. Draft release notes for the next tag (draft only — do not tag)

Suggested tag: **v1.1.0** (backwards-compatible feature release; requires a
`pyproject.toml` version bump and a `CITATION.cff`/README software-version
update at release time — neither is done on the branch).

> ### v1.1.0 — Switched-linear extension
>
> DPSpice now simulates gate-driven switched circuits. `Sxxx` switch
> elements with independent `PULSE` gates are routed automatically and
> solved on one of three paths: **LTP harmonic balance** (periodic steady
> state in a single linear solve), **envelope-bank transient** (start-up
> dynamics of the harmonic envelopes), and **hybrid NR-HB** for circuits
> mixing switches and diodes. New CLI surface: `dpspice route`,
> `dpspice run --analysis {hb,envelope}`; new API:
> `dpspice.route/solve_hb/solve_envelope`. Seven bundled converter
> examples, an external validation campaign (`VALIDATION_REPORT.md`,
> LTspice cross-checks with settled tails and finite gate edges, an
> in-repo switched-DAE transient reference), and 40+ new tests.
>
> **The hybrid switch+diode path is experimental.** It is validated on a
> narrower circuit population than the LTP and envelope paths; quote a
> hybrid DC at moderate K with a transient cross-check.
>
> Two validity limits of the LTP path are documented rather than hidden:
>
> 1. **O(1/K) DC truncation bias at stiff switched nodes** (duty-dependent,
>    can exceed the ripple at high duty). Two mitigations ship:
>    `--bias-correction` (closed-form, parameter-free) and `--richardson`
>    (K-extrapolation). They correct the same defect and are mutually
>    exclusive.
> 2. **The bias correction is contractive only in a bounded regime.** A
>    stiff switched node at high duty (low R·C·f_sw) can put its
>    fixed-point iteration outside the contraction region at any practical
>    K; it then diverges and fails loudly (`converged=False`, typed
>    CLI/dispatch error) — use `--richardson` in that regime.
>
> The rectifier/dynamic-phasor core is untouched: the v1.0.5 benchmark
> solution is byte-identical (`MERGE_CHECKLIST.md`, item 3).
>
> The switched-linear extension is not covered by the ECCE 2026 paper; a
> paper on it is in preparation. Cite the software concept DOI
> (10.5281/zenodo.21085058) for this functionality until then.

The "paper in preparation" note is also in the README next to the citation
section (added in `89c55bb`).

## 7. Open maintainer decisions (nothing executed)

1. **Merge** `feature/switched-linear` → `main`. All evidence above is
   green; ready from this audit's perspective.
2. **Tag** the next release (suggested `v1.1.0`). Requires: `pyproject.toml`
   version bump, `CITATION.cff` + README software-BibTeX version update,
   moving the CHANGELOG `[Unreleased]` block under the new tag. None of
   this is done on the branch (freeze).
3. **Push** — nothing has been pushed; the branch is local-only.
4. **Zenodo** — deposit a new version under concept DOI
   10.5281/zenodo.21085058 after tagging, or defer archiving until the
   switched-linear paper is submitted.
5. **`exploration/basis-selection/`** — whether it ever joins the public
   repo. **Recommendation on file: it does not.** Its findings that matter
   to users are already back-propagated (README limitation,
   VALIDATION_REPORT ledger B1, CHANGELOG note, regression test in
   `b0599ee`/`e817d05`); the prototype itself is research scaffolding for
   paper-2 (per-interval exact solver, crossover study, K* rule) and would
   otherwise become a de-facto supported API surface. Keep it untracked or
   move it to a private research repo; archive `FINDINGS.md` alongside the
   paper-2 draft.
