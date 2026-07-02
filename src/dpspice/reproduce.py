"""Regenerate the paper's computational and accuracy artifacts.

Every number this module reports comes from a real solve over a bundled
example netlist (and, where accuracy is claimed, a real LTspice ``.raw``
reference shipped alongside it). Nothing is hard-coded or fabricated.

The figure/table identifiers below map onto the artifacts that the public
release can reproduce from its bundled examples. They are intentionally
honest about scope: the full paper sweep (all RLC Q values, the WPT link,
the IEEE timing tables) needs data that is not redistributed here, so those
entries say so rather than inventing numbers.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import dispatch
from .dispatch import DpspiceError
from . import crossval as _validate
from .examples import example_text, example_path


# The bundled cases, each tagged with the solver path it exercises.
BENCH_CASES = [
    ("rlc.sp", "series RLC (linear)"),
    ("rectifier_halfwave.sp", "half-wave rectifier (nonlinear)"),
    ("rectifier_rc.sp", "cap-smoothed rectifier (nonlinear)"),
]


# ----------------------------------------------------------------------
# bench: computational benchmark over the bundled examples
# ----------------------------------------------------------------------

def bench() -> List[Dict]:
    """Run every bundled example through the real engine, return timing rows.

    Each row is JSON-serialisable: ``case``, ``solver``, ``states``, ``K``
    (or ``None`` for linear), ``solve_ms``. The numbers are whatever the
    solver actually took on this machine for this run.
    """
    rows: List[Dict] = []
    for filename, label in BENCH_CASES:
        try:
            result = dispatch.run(example_text(filename), with_waveforms=False)
        except DpspiceError as exc:
            rows.append({"case": label, "solver": "error", "states": 0,
                         "K": None, "solve_ms": 0.0, "error": str(exc)})
            continue
        rows.append({
            "case": label,
            "solver": result.solver,
            "states": result.n_states,
            "K": result.K,
            "solve_ms": result.solve_time_s * 1000.0,
        })
    return rows


# ----------------------------------------------------------------------
# reproduce: figure / table registry
# ----------------------------------------------------------------------

def duration_sweep(periods=(12, 50, 200)) -> List[Dict]:
    """Per-duration IDP-vs-TD accuracy and speedup on the bundled series-RLC.

    Both IDP and the full time-domain solve are DPSpice's own paths, so this is
    fully reproducible offline (no external reference needed). Each row reports
    the simulated window, IDP and TD solve times, their ratio (the speedup),
    and the IDP-vs-TD NRMSE / R^2 of V(out).

    The accuracy degrades and the speedup grows as the window lengthens: IDP
    cost is flat (it tracks a constant envelope) while TD cost scales with the
    number of carrier cycles. The machine-independent invariant is the speedup
    *trend* (~tenfold per decade of duration), not the absolute milliseconds.
    """
    import time as _time
    import numpy as _np
    from . import compare as _compare

    f = 580e3 / (2 * _np.pi)  # series-RLC resonance ~580 krad/s

    def _netlist(nper):
        return (f"V1 in 0 SINE(0 1 {f:.6g})\nR1 in n2 3.0\nL1 n2 out 100.04u\n"
                f"C1 out 0 30.07n\nR2 out 0 2k\n.tran 0 {nper / f:.6g}\n.end\n")

    def _vout(result):
        for w in result.waveforms:
            if w.name.lower() == "v(out)":
                return _np.asarray(w.t, float), _np.asarray(w.v, float)
        raise DpspiceError("V(out) not in solve output")

    rows: List[Dict] = []
    for nper in periods:
        nl = _netlist(nper)
        t0 = _time.perf_counter(); ri = dispatch.run(nl, mode="idp"); idp_s = _time.perf_counter() - t0
        t0 = _time.perf_counter(); rt = dispatch.run(nl, mode="td"); td_s = _time.perf_counter() - t0
        ti, vi = _vout(ri)
        tt, vt = _vout(rt)
        m = _compare.compare_on_time_grid(tt, vt, ti, vi,
                                          grid_points=min(len(ti), len(tt)))
        rows.append({
            "periods": int(nper),
            "sim_window_s": nper / f,
            "idp_ms": idp_s * 1000.0,
            "td_ms": td_s * 1000.0,
            "speedup": td_s / max(idp_s, 1e-12),
            "nrmse": float(m["nrmse"]),
            "r2": float(m["r2"]),
        })
    return rows


def _table_benchmark() -> Dict:
    return {
        "label": "Table III: computational benchmark (bundled examples)",
        "kind": "table",
        "rows": bench(),
        # Per-duration accuracy + speedup (IDP vs full TD on the RLC case). This
        # is the offline-reproducible analog of the paper's per-duration speedup
        # envelope and carries the Table II (IDP-vs-TD accuracy) content; the
        # IEEE-network figures themselves need external case files
        # (see Table IV / REPRODUCIBILITY.md).
        "idp_vs_td_duration_sweep": duration_sweep(),
        "note": "Solve times are machine-dependent; state counts, solver "
                "selection, NRMSE/R^2 and the speedup *trend* are deterministic.",
    }


# Table V, row by row. Each rectifier load enters discontinuous conduction to a
# different degree, so each needs the harmonic count its conduction pulse demands
# (the paper reports per-row K, not one global K): the smooth resistive load
# converges at K=15, the mild RC ripple at K=30, the strong DCM headline case at
# K=40. Every row solves a bundled netlist and cross-checks against a bundled
# LTspice ``.raw`` from the same deck. Nothing here is hard-coded.
RECTIFIER_CASES = [
    ("resistive", None, "rectifier_halfwave.sp", "rectifier_halfwave.raw", 15),
    ("RC, mild", "10uF", "rectifier_rc_mild.sp", "rectifier_rc_mild.raw", 30),
    ("RC, strong", "100uF", "rectifier_rc.sp", "rectifier_rc.raw", 40),
]


def _table_accuracy() -> Dict:
    """Reproduce all three Table V rows from real solves + bundled LTspice refs.

    For each load the row carries the solved conduction angle (from the HB
    summary, no external data needed) and the NRMSE / R^2 / dc output against the
    bundled LTspice reference for that exact deck. ``worst_nrmse`` is the
    poorest row, so a single number still guards the whole table in regression.
    """
    rows: List[Dict] = []
    for case, cap, sp_name, raw_name, K in RECTIFIER_CASES:
        netlist = example_text(sp_name)
        run = dispatch.run(netlist, harmonics=K, with_waveforms=False)
        with example_path(raw_name) as raw:
            report = _validate.validate(netlist, raw, harmonics=K)
        out = next(p for p in report["per_node"] if p["node"].lower() == "out")
        rows.append({
            "case": case,
            "C": cap,
            "K": K,
            "reference": raw_name,
            "conduction_angle_deg": run.summary.get("conduction_angle_deg"),
            "nrmse_vs_ltspice": out["nrmse"],
            "r2": out["r2"],
            "vdc_ltspice": out["dc_ref"],
            "vdc_hb": out["dc_test"],
        })
    return {
        "label": "Table V: accuracy vs LTspice (half-wave rectifier, three loads)",
        "kind": "table",
        "rows": rows,
        "worst_nrmse": max(r["nrmse_vs_ltspice"] for r in rows),
    }


def _figure_rectifier_waveform() -> Dict:
    """Fig. 6 waveforms: the capacitor-smoothed (RC-strong, C=100 uF) rectifier
    in discontinuous conduction, from the real HB solve at the paper's K=40."""
    netlist = example_text("rectifier_rc.sp")
    result = dispatch.run(netlist, harmonics=40, with_waveforms=True)
    waves = [{"name": w.name, "t": w.t, "v": w.v} for w in result.waveforms]
    return {
        "label": "Figure 6: capacitor-smoothed rectifier waveforms (harmonic balance)",
        "kind": "figure",
        "reference": "rectifier_rc.sp",
        "solver": result.solver,
        "K": result.K,
        "conduction_angle_deg": result.summary.get("conduction_angle_deg"),
        "waveforms": waves,
    }


# Registry maps a (kind, number) to a builder. Numbers follow the paper's
# final (v10) numbering; entries marked external need data not redistributed in
# this repo. v10 numbering: Table III = computational benchmark, Table IV = IEEE
# network timing, Table V = rectifier accuracy vs LTspice, Fig. 6 = rectifier
# output waveform. (The RLC accuracy vs LTspice lives in paper Sec. III-A and the
# offline IDP-vs-TD accuracy is Table II, carried inside --table 3 below; the
# coupled/WPT accuracy is Sec. III-E / Fig. 8. Neither is a redistributable
# numbered artifact, so both are reached via `dpspice validate` with your .raw.)
_REGISTRY = {
    ("table", 3): ("Computational benchmark (Table III)", _table_benchmark),
    ("table", 5): ("Accuracy vs LTspice (rectifier, Table V)", _table_accuracy),
    ("figure", 6): ("Rectifier output waveform (Fig. 6)", _figure_rectifier_waveform),
}

# Documented but not redistributable from this repo (no fabrication).
_EXTERNAL = {
    ("table", 4): "IEEE-network timing tables (Table IV) — needs the IEEE case "
                  "files; see the validation suite for a steady-state smoke test.",
}


def reproduce(figure: Optional[int] = None, table: Optional[int] = None) -> Dict:
    """Reproduce one figure/table from the real engine."""
    if figure is not None and table is not None:
        raise DpspiceError("Pass either --figure or --table, not both.")
    if figure is not None:
        key = ("figure", figure)
    elif table is not None:
        key = ("table", table)
    else:
        raise DpspiceError("Specify --figure N or --table N (run with no args to list).")

    if key in _REGISTRY:
        _label, builder = _REGISTRY[key]
        return builder()
    if key in _EXTERNAL:
        raise DpspiceError(_EXTERNAL[key])
    kind, num = key
    raise DpspiceError(
        f"No reproducible artifact registered for {kind} {num}. "
        f"Run `dpspice reproduce` with no arguments to see what is available."
    )


def catalogue() -> Dict:
    """A JSON-able listing of what this release can reproduce.

    This is the structured form behind ``dpspice reproduce`` with no
    ``--table``/``--figure``: it powers both the ``--json`` listing and the
    plain ``--quiet`` listing, so neither path ever falls back to decorated
    output.
    """
    available = [
        {"kind": kind, "number": num, "flag": f"--{kind} {num}",
         "label": label, "status": "reproducible"}
        for (kind, num), (label, _builder) in sorted(_REGISTRY.items())
    ]
    external = [
        {"kind": kind, "number": num, "flag": f"--{kind} {num}",
         "label": why.split(" — ")[0], "status": "needs-external-data",
         "detail": why}
        for (kind, num), why in sorted(_EXTERNAL.items())
    ]
    return {
        "label": "dpspice reproduce — available artifacts",
        "available": available,
        "external": external,
        "hint": "Pass --table N or --figure N to regenerate one artifact.",
    }


def catalogue_table():
    """A Rich table of what this release can reproduce (CLI display helper)."""
    from rich.table import Table  # lazy: keeps the core import free of rich
    t = Table(title="dpspice reproduce — available artifacts",
              title_style="bold", header_style="bold")
    t.add_column("flag")
    t.add_column("artifact")
    t.add_column("status")
    for (kind, num), (label, _builder) in sorted(_REGISTRY.items()):
        t.add_row(f"--{kind} {num}", label, "[green]reproducible[/green]")
    for (kind, num), why in sorted(_EXTERNAL.items()):
        t.add_row(f"--{kind} {num}", why.split(" — ")[0], "[yellow]needs external data[/yellow]")
    return t
