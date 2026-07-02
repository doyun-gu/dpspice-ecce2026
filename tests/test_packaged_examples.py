"""Section 5 - bundled examples ship as importable package resources.

The release blocker this guards: in a non-editable (wheel/zipapp) install the
example netlists and the bundled ``.raw`` reference are NOT next to the repo
root, so any code that located them via a ``__file__``-relative or
current-working-directory path failed with "Could not locate the bundled
examples/ directory". The fix routes every lookup through
:mod:`importlib.resources` (``dpspice.examples``), which resolves identically in
editable, wheel, and zipapp installs.

These tests run the ``bench`` / ``reproduce`` paths from a *foreign working
directory* (``monkeypatch.chdir`` into a tmp dir with no ``examples/``), proving
the resolution does not depend on cwd or a repo checkout. If the regression
returns, these fail regardless of how the package was installed.
"""
from __future__ import annotations

from importlib import resources

import pytest

import dpspice
from dpspice import reproduce


def test_examples_are_packaged_resources():
    """The example files must be discoverable as packaged resources."""
    names = set(dpspice.list_examples())
    assert {"rlc.sp", "rectifier_halfwave.sp", "rectifier_rc.sp",
            "rectifier_rc_mild.sp", "rectifier_halfwave.raw",
            "rectifier_rc.raw", "rectifier_rc_mild.raw"} <= names
    # And the resource really resolves under the installed package.
    res = resources.files("dpspice").joinpath("data", "examples", "rlc.sp")
    assert res.is_file()
    assert "V1" in dpspice.example_text("rlc.sp")


def test_example_path_yields_real_file_for_binary_ref(tmp_path, monkeypatch):
    """The binary ``.raw`` reference must be reachable as a real filesystem path
    even from a foreign cwd (the validate path opens it by path)."""
    monkeypatch.chdir(tmp_path)
    with dpspice.example_path("rectifier_halfwave.raw") as p:
        with open(p, "rb") as fh:
            head = fh.read(8)
    assert head  # non-empty binary read succeeded


def test_bench_runs_from_foreign_cwd(tmp_path, monkeypatch):
    """``reproduce.bench`` must work without a repo-relative ``examples/``."""
    monkeypatch.chdir(tmp_path)
    rows = reproduce.bench()
    assert rows and all(r["solver"] != "error" for r in rows), rows


def test_reproduce_table_and_figure_run_from_foreign_cwd(tmp_path, monkeypatch):
    """``reproduce --table 3/5`` and ``--figure 6`` must resolve bundled data
    via package resources, not the source tree."""
    monkeypatch.chdir(tmp_path)
    t3 = reproduce.reproduce(table=3)
    assert t3["rows"] and "idp_vs_td_duration_sweep" in t3
    t5 = reproduce.reproduce(table=5)
    assert len(t5["rows"]) == 3
    assert {r["reference"] for r in t5["rows"]} == {
        "rectifier_halfwave.raw", "rectifier_rc_mild.raw", "rectifier_rc.raw"}
    assert t5["worst_nrmse"] >= 0.0
    f6 = reproduce.reproduce(figure=6)
    assert f6["waveforms"]
    assert f6["reference"] == "rectifier_rc.sp"
