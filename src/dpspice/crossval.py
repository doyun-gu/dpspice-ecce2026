"""Single-circuit cross-validation against an independent oracle.

``dpspice validate <netlist>`` runs DPSpice and an independent reference, then
reports NRMSE / R^2 / max-abs-error per matched node. The reference is either:

* a user-supplied ``.raw`` (``--ref out.raw``), read with the engine's binary
  reader (LTspice and ngspice share the same ``Binary:`` real-double format), or
* an ngspice run that this module drives automatically (``ref=None``), when
  ngspice is on PATH.

The comparison metrics live in :mod:`dpspice.compare` so the single-circuit
path and the batch suite compute identical numbers. Everything flows through
the real engine and a real reference binary; no numbers are fabricated.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

from . import dispatch
from . import compare
from .dispatch import DpspiceError, read_netlist
from ._ngspice import run_ngspice, ngspice_available, NgspiceError

# engine modules (sys.path already wired by importing dispatch -> _engine)
from netlist_parser import parse_ltspice_netlist  # noqa: E402
from mna_circuit import NetlistCircuit            # noqa: E402


def validate(source: str, ref: Optional[str] = None, mode: str = "auto",
             harmonics: Optional[int] = None,
             omega_hz: Optional[float] = None,
             tol: Optional[float] = None,
             grid_points: int = 2000,
             keep_raw: bool = False) -> Dict:
    """Run + cross-validate. Returns a JSON-able report dict.

    Args:
        source: netlist path or text.
        ref: path to a reference ``.raw``; if ``None``, ngspice is run
            automatically (raises a clear error if ngspice is unavailable).
        keep_raw: when auto-running ngspice, keep the generated ``.raw`` and
            report its path under ``reference``.
    """
    from ltspice_io import read_raw, trace   # noqa: E402  rectifier/ltspice_io.py
    import metrics as M                        # noqa: E402

    netlist_str = read_netlist(source)
    netlist = parse_ltspice_netlist(netlist_str)
    info = dispatch.analyze(netlist_str, mode=mode, omega_hz=omega_hz)
    f0 = info.omega_hz
    if f0 is None:
        raise DpspiceError("Validation needs a carrier frequency; pass --omega <Hz>.")
    dispatch._require_tran(netlist, info.mode_selected)

    # ---- obtain the reference .raw -----------------------------------------
    reference_label = ref
    reference_engine = "user"
    if ref is None:
        if not ngspice_available():
            raise DpspiceError(
                "No --ref given and ngspice is not installed. Install ngspice "
                "(macOS: `brew install ngspice`) or pass --ref your.raw."
            )
        try:
            raw_path, _deck = run_ngspice(netlist_str, keep=keep_raw)
        except NgspiceError as exc:
            raise DpspiceError(str(exc)) from exc
        ref = raw_path
        reference_label = raw_path if keep_raw else "ngspice (auto)"
        reference_engine = "ngspice"

    try:
        names, data = read_raw(ref)
    except FileNotFoundError:
        raise DpspiceError(f"Reference .raw not found: {ref}")
    except Exception as exc:
        raise DpspiceError(f"Could not read reference .raw '{ref}': {exc}") from exc
    t_ref = trace(names, data, "time")
    ref_nodes = {n[2:-1] for n in names if n.startswith("v(") and n.endswith(")")}

    per_node: List[Dict] = []

    if info.mode_selected == "hb":
        result, _diodes, _dnl, _dt = dispatch.solve_hb_native(
            netlist_str, netlist, f0, harmonics, tol)
        engine_nodes = [n for n in netlist.non_ground_nodes()
                        if result.hbnet.idx(n) >= 0]
        ph = np.linspace(0, 1, grid_points, endpoint=False)
        # ngspice lowercases node names; match case-insensitively.
        for node in sorted(n for n in engine_nodes if n.lower() in ref_nodes):
            v_ref_raw = trace(names, data, f"V({node})")
            _, v_hb, v_ref, _nr = M.align_and_nrmse(result, node, t_ref, v_ref_raw,
                                                    f0, M=grid_points)
            m = compare.compare_on_phase_grid(ph, v_ref, v_hb)
            per_node.append({"node": node, **m})
        meta = {"solver": "hb", "K": int(result.K),
                "converged": bool(result.converged)}
    else:
        circuit = NetlistCircuit.from_string(netlist_str)
        if info.mode_selected == "td":
            res = circuit.solve_time_domain()
        else:
            circuit.configure_phasor(omega_s=2 * np.pi * f0)
            res = circuit.solve_phasor_domain()
        t_sim = np.asarray(res["t"], dtype=float)
        sim_nodes = {k[2:-1] for k in res if k.startswith("V(") and k.endswith(")")}
        for node in sorted(n for n in sim_nodes if n.lower() in ref_nodes):
            v_sim = np.real(np.asarray(res[f"V({node})"]))
            v_ref_raw = trace(names, data, f"V({node})")
            m = compare.compare_on_time_grid(t_ref, v_ref_raw, t_sim, v_sim,
                                             grid_points=grid_points)
            per_node.append({"node": node, **m})
        meta = {"solver": info.mode_selected}

    if not per_node:
        raise DpspiceError(
            f"No common nodes between solve output and the reference. "
            f"Reference has {sorted(ref_nodes)}; check node names match."
        )

    return {
        "reference": reference_label,
        "reference_engine": reference_engine,
        "omega_hz": f0,
        "mode_selected": info.mode_selected,
        "reason": info.reason,
        **meta,
        "per_node": per_node,
        "worst_nrmse": max(p["nrmse"] for p in per_node),
        "min_r2": min(p["r2"] for p in per_node),
        "max_abs_error": max(p["max_abs_error"] for p in per_node),
    }
