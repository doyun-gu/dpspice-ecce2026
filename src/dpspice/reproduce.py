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
        "label": "Table: computational benchmark (bundled examples)",
        "kind": "table",
        "rows": bench(),
        # Per-duration accuracy + speedup (IDP vs full TD on the RLC case). This
        # is the offline-reproducible analog of the paper's per-duration speedup
        # envelope; the IEEE-network figures themselves need external case files
        # (see Table 5 / REPRODUCIBILITY.md).
        "idp_vs_td_duration_sweep": duration_sweep(),
        "note": "Solve times are machine-dependent; state counts, solver "
                "selection, NRMSE/R^2 and the speedup *trend* are deterministic.",
    }


def _table_accuracy() -> Dict:
    """Accuracy vs the bundled LTspice rectifier reference (real validate run)."""
    netlist = example_text("rectifier_halfwave.sp")
    with example_path("rectifier_halfwave.raw") as raw:
        report = _validate.validate(netlist, raw)
    return {
        "label": "Table: accuracy vs LTspice (half-wave rectifier)",
        "kind": "table",
        "reference": "rectifier_halfwave.raw",
        "solver": report["solver"],
        "K": report.get("K"),
        "per_node": report["per_node"],
        "worst_nrmse": report["worst_nrmse"],
        "min_r2": report["min_r2"],
    }


def _figure_rectifier_waveform() -> Dict:
    """Half-wave rectifier output waveform from the real HB solve."""
    netlist = example_text("rectifier_halfwave.sp")
    result = dispatch.run(netlist, with_waveforms=True)
    waves = [{"name": w.name, "t": w.t, "v": w.v} for w in result.waveforms]
    return {
        "label": "Figure: half-wave rectifier waveforms (harmonic balance)",
        "kind": "figure",
        "solver": result.solver,
        "K": result.K,
        "conduction_angle_deg": result.summary.get("conduction_angle_deg"),
        "waveforms": waves,
    }


# Registry maps a (kind, number) to a builder. Numbers follow the paper's
# numbering; entries marked external need data not redistributed in this repo.
_REGISTRY = {
    ("table", 3): ("Computational benchmark", _table_benchmark),
    ("table", 4): ("Accuracy vs LTspice (rectifier)", _table_accuracy),
    ("figure", 5): ("Rectifier output waveform", _figure_rectifier_waveform),
}

# Documented but not redistributable from this repo (no fabrication).
_EXTERNAL = {
    ("table", 1): "RLC Q-sweep NRMSE — needs the full LTspice reference set "
                  "(not redistributed). Use `dpspice validate` with your own .raw.",
    ("table", 2): "WPT k=0.2 link accuracy — needs the coupled-link LTspice "
                  "reference (not redistributed).",
    ("table", 5): "IEEE-network timing tables — needs the IEEE case files; "
                  "see the validation suite for a steady-state smoke test.",
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
