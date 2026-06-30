"""The batch validation suite: generator + oracle pairs, run and scored.

For every generated circuit we run DPSpice and an independent oracle (closed
-form analytic, or ngspice), compare on a shared grid using the same metrics
as ``dpspice validate``, and classify against the circuit's family-specific
tolerance band. "It ran" is never treated as "it is right": a case only
PASSES if its error is within its band relative to a real reference.

Nothing here fabricates numbers; every value comes from a real solve vs a real
reference. ngspice divergence on a generated circuit yields SKIP (no reference
available), not a DPSpice failure.
"""
from __future__ import annotations

from statistics import median
from typing import Dict, List, Optional

import numpy as np

from .. import api
from ..dispatch import DpspiceError
from .. import compare
from .._ngspice import ngspice_available
from . import families
from .families import Case


# ----------------------------------------------------------------------
# running one case
# ----------------------------------------------------------------------

def _dpspice_waveform(netlist: str, node: str):
    """Run DPSpice and return (t, v) for ``V(node)``."""
    result = api.load(netlist).run(with_waveforms=True)
    want = f"V({node})".lower()
    for w in result.waveforms:
        if w.name.lower() == want:
            return np.asarray(w.t), np.asarray(w.v), result
    raise DpspiceError(f"node '{node}' not found in DPSpice output")


def _run_closed_form(case: Case) -> Dict:
    t, v, result = _dpspice_waveform(case.netlist, case.output_node)
    v_ref = case.analytic(t)
    m = compare.compare_on_time_grid(t, v_ref, t, v, grid_points=len(t))
    return {"metrics": m, "states": result.states, "K": result.K,
            "solve_ms": result.solve_time * 1000.0}


def _run_ngspice(case: Case) -> Dict:
    """Use the shared validate path (auto ngspice) and pick the output node."""
    val = api.load(case.netlist).validate(ref=None)
    report = val.to_dict()
    node_l = case.output_node.lower()
    chosen = next((p for p in report["per_node"] if p["node"].lower() == node_l), None)
    if chosen is None:
        # fall back to the worst node so we still score something meaningful
        chosen = max(report["per_node"], key=lambda p: p["nrmse"])
    return {"metrics": chosen, "states": None, "K": report.get("K"),
            "solve_ms": None, "node_used": chosen["node"]}


def run_case(case: Case) -> Dict:
    """Run one case, classify it, return a JSON-able record."""
    rec = {
        "family": case.family, "name": case.name, "oracle": case.oracle,
        "band": case.band, "output_node": case.output_node,
        "status": None, "nrmse": None, "r2": None, "max_abs_error": None,
        "solve_ms": None, "states": None, "K": None, "reason": "",
        "borderline": False, "netlist": case.netlist,
    }
    try:
        if case.oracle == "closed_form":
            out = _run_closed_form(case)
        else:
            out = _run_ngspice(case)
    except DpspiceError as exc:
        msg = str(exc)
        # ngspice missing / diverged -> no reference -> SKIP, not a solver failure.
        low = msg.lower()
        if case.oracle == "ngspice" and ("ngspice" in low or "diverg" in low
                                         or "did not produce" in low):
            rec["status"] = "skip"
            rec["reason"] = msg
        else:
            rec["status"] = "fail"
            rec["reason"] = f"solver error: {msg}"
        return rec
    except np.linalg.LinAlgError as exc:
        # The engine's index-1 DAE reduction could not be constructed for this
        # topology (e.g. series-compensated coupled resonators). A real,
        # specific solver limitation, reported as such rather than hidden.
        rec["status"] = "fail"
        rec["reason"] = f"engine could not build the DAE for this topology: {exc}"
        return rec
    except Exception as exc:  # unexpected -> fail loudly with the netlist
        rec["status"] = "fail"
        rec["reason"] = f"unexpected error: {type(exc).__name__}: {exc}"
        return rec

    m = out["metrics"]
    rec["nrmse"] = float(m["nrmse"])
    rec["r2"] = float(m.get("r2", float("nan")))
    rec["max_abs_error"] = float(m.get("max_abs_error", float("nan")))
    rec["states"] = out.get("states")
    rec["K"] = out.get("K")
    rec["solve_ms"] = out.get("solve_ms")
    if "node_used" in out:
        rec["output_node"] = out["node_used"]

    if not np.isfinite(rec["nrmse"]):
        rec["status"] = "fail"
        rec["reason"] = "non-finite NRMSE"
    elif rec["nrmse"] <= case.band:
        rec["status"] = "pass"
        if rec["nrmse"] > 0.7 * case.band:
            rec["borderline"] = True
            rec["reason"] = "within band but near the threshold"
    else:
        rec["status"] = "fail"
        rec["reason"] = (f"NRMSE {rec['nrmse']:.4%} exceeds family band "
                         f"{case.band:.4%}")
    return rec


# ----------------------------------------------------------------------
# harness self-check: do the two oracles agree on series RLC?
# ----------------------------------------------------------------------

def self_check(quick: bool = True) -> List[Dict]:
    """For each RLC case, compare the closed-form oracle directly against
    ngspice (independent of DPSpice). Small NRMSE confirms the harness itself
    is trustworthy before we judge the solver with it.
    """
    from .._ngspice import run_ngspice, NgspiceError
    from ltspice_io import read_raw, trace  # noqa: E402  engine module

    out = []
    for case in families.gen_series_rlc(quick):
        row = {"name": case.name, "status": None, "nrmse": None, "reason": ""}
        try:
            raw_path, _ = run_ngspice(case.netlist)
            names, data = read_raw(raw_path)
            t = trace(names, data, "time")
            v_ng = trace(names, data, f"V({case.output_node})")
            v_cf = case.analytic(t)
            m = compare.compare_on_time_grid(t, v_cf, t, v_ng, grid_points=len(t))
            row["nrmse"] = float(m["nrmse"])
            row["status"] = "agree" if m["nrmse"] < 0.01 else "disagree"
        except NgspiceError as exc:
            row["status"] = "skip"
            row["reason"] = str(exc)
        out.append(row)
    return out


# ----------------------------------------------------------------------
# whole suite
# ----------------------------------------------------------------------

def run_suite(quick: bool = False, only: Optional[List[str]] = None) -> Dict:
    cases = families.all_cases(quick=quick, only=only)
    ng_needed = any(c.oracle == "ngspice" for c in cases)
    ng_ok = ngspice_available()

    records = [run_case(c) for c in cases]

    # aggregate per family
    fam_summary: Dict[str, Dict] = {}
    for rec in records:
        fam = rec["family"]
        s = fam_summary.setdefault(fam, {"passed": 0, "failed": 0, "skipped": 0,
                                         "nrmse": []})
        if rec["status"] == "pass":
            s["passed"] += 1
        elif rec["status"] == "fail":
            s["failed"] += 1
        else:
            s["skipped"] += 1
        if rec["nrmse"] is not None and np.isfinite(rec["nrmse"]):
            s["nrmse"].append(rec["nrmse"])

    for s in fam_summary.values():
        ns = s.pop("nrmse")
        s["nrmse_min"] = min(ns) if ns else None
        s["nrmse_median"] = median(ns) if ns else None
        s["nrmse_max"] = max(ns) if ns else None

    totals = {
        "passed": sum(1 for r in records if r["status"] == "pass"),
        "failed": sum(1 for r in records if r["status"] == "fail"),
        "skipped": sum(1 for r in records if r["status"] == "skip"),
        "borderline": sum(1 for r in records if r.get("borderline")),
    }
    warnings = []
    if ng_needed and not ng_ok:
        warnings.append("ngspice not installed: ngspice-oracle families were "
                        "skipped. Install ngspice for full coverage.")
    if only is None and families.EXPERIMENTAL:
        excluded = ", ".join(sorted(families.EXPERIMENTAL))
        warnings.append(f"experimental families excluded from the default run "
                        f"({excluded}); run `--family {sorted(families.EXPERIMENTAL)[0]}` "
                        f"to exercise a known engine limitation.")

    return {
        "quick": quick,
        "families": only or families.DEFAULT_FAMILIES,
        "ngspice_available": ng_ok,
        "totals": totals,
        "per_family": fam_summary,
        "cases": records,
        "warnings": warnings,
    }
