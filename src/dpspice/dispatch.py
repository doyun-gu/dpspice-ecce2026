"""Tier 1/2/3 auto-run logic: netlist in, result out.

This module is the only place that knows about the vendored engine. It owns:

* **Tier 1** (deterministic from the netlist): parse, build MNA, count states,
  read the ``.tran`` window.
* **Tier 2** (auto-estimated, announced, overridable): pick the analysis mode
  (IDP vs harmonic-balance), extract the carrier frequency, choose the harmonic
  count K.
* **Tier 3** (user overrides): ``mode`` / ``harmonics`` / ``omega`` / ``tol``.

Every Tier-2 guess is recorded as a :class:`~dpspice.results.Decision` so the
caller can show *decide, announce, allow override*.
"""
from __future__ import annotations

import os
import re
import time
from typing import List, Optional, Tuple

import numpy as np

from .results import Decision, InfoResult, RunResult, Waveform

# Importing the vendored engine package wires up sys.path for the flat modules.
from . import _engine  # noqa: F401  (side-effect import)

from netlist_parser import parse_ltspice_netlist, parse_spice_value, SourceType  # noqa: E402
from mna_circuit import NetlistCircuit, build_mna              # noqa: E402

# Default / cap harmonic counts for the adaptive HB sweep.
DEFAULT_K = 20
K_LADDER = [20, 40, 64]

# Element prefixes the parser tags as semiconductors.
_NONLINEAR_PREFIXES = {"D", "M", "Q"}


class DpspiceError(Exception):
    """Raised with an actionable message (never a bare stack trace to the user)."""


# ----------------------------------------------------------------------
# Input handling
# ----------------------------------------------------------------------

def read_netlist(source: str) -> str:
    """Accept either a netlist *string* or a path to a ``.sp/.cir/.net`` file.

    The MCP server passes raw netlist text; the CLI usually passes a path.
    A heuristic distinguishes them: a single-line token that exists on disk is
    a path; anything containing newlines or SPICE element lines is text.
    """
    looks_like_path = (
        os.path.sep in source or source.lower().endswith((".sp", ".cir", ".net"))
    ) and "\n" not in source.strip()
    if looks_like_path:
        if not os.path.isfile(source):
            raise DpspiceError(f"Netlist file not found: {source}")
        with open(source, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    return source


# ----------------------------------------------------------------------
# Input validation (the error catalogue)
#
# These checks turn the failure modes an adversarial caller will hit into
# clear, actionable :class:`DpspiceError` messages instead of bare engine
# stack traces. Every interface (Python API, CLI, MCP — and a future HTTP
# layer) routes through this module, so implementing the messages here makes
# them consistent everywhere: the API raises, the CLI prints + exits non-zero,
# and the MCP server returns the text.
# ----------------------------------------------------------------------

#: Soft ceiling on MNA state count for the pure-Python dense backend. The dense
#: factorisations are O(n^3) time / O(n^2) memory, so beyond a few thousand
#: states a solve can exhaust memory. We refuse with a clear message rather
#: than letting it hard-crash. Overridable via ``DPSPICE_MAX_STATES`` (e.g. to
#: lift the cap on a big machine, or lower it in tests).
_DEFAULT_MAX_STATES = 20000


def _max_states() -> int:
    try:
        return int(os.environ.get("DPSPICE_MAX_STATES", _DEFAULT_MAX_STATES))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_STATES


def _logical_lines(netlist_str: str) -> List[str]:
    """Reconstruct logical netlist lines the way the engine parser does:
    strip inline ``;`` comments, drop ``*`` comment lines, and fold ``+``
    continuation lines onto the previous line.
    """
    logical: List[str] = []
    for raw in netlist_str.splitlines():
        s = raw.strip()
        if not s:
            continue
        cpos = s.find(";")
        if cpos >= 0:
            s = s[:cpos].rstrip()
        if not s or s.startswith("*"):
            continue
        if s.startswith("+") and logical:
            logical[-1] = logical[-1] + " " + s[1:].strip()
        else:
            logical.append(s)
    return logical


_RLC_UNIT = {"R": ("ohms", "1k"), "L": ("henries", "1m"), "C": ("farads", "4.7u")}


def _precheck_netlist(netlist_str: str, netlist) -> None:
    """Catch malformed element lines before the engine silently mis-stamps them.

    The vendored parser tolerantly defaults a missing/garbled R/L/C value to
    0 (and the MNA build then "skips" it), which turns a typo into a wrong but
    silent answer. We refuse those lines here with a message that names the
    fix. Legitimate values (including an explicit ``0``) pass untouched.
    """
    params = getattr(netlist, "params", {}) or {}
    for s in _logical_lines(netlist_str):
        if s.startswith("."):
            continue
        tokens = s.split()
        if not tokens:
            continue
        name = tokens[0]
        prefix = name[0].upper()
        if prefix in _RLC_UNIT:
            unit, example = _RLC_UNIT[prefix]
            if len(tokens) < 4:
                raise DpspiceError(
                    f"Malformed component line '{s}': {name} needs "
                    f"'<node+> <node-> <value>' (3 fields) but got "
                    f"{len(tokens) - 1}. Example: {name} a b {example}"
                )
            posval = next((t for t in tokens[3:]
                           if "=" not in t and not t.upper().startswith("IC=")), None)
            if posval is None:
                raise DpspiceError(
                    f"Malformed component line '{s}': {name} has no numeric "
                    f"value ({unit}). Example: {name} a b {example}"
                )
            if "{" not in posval:
                try:
                    parse_spice_value(posval, params)
                except ValueError:
                    raise DpspiceError(
                        f"Malformed component line '{s}': could not read a "
                        f"numeric value for {name} from '{posval}'. Use SPICE "
                        f"notation like {example} (k, m, u, n, p, meg)."
                    )
        elif prefix in ("V", "I"):
            if len(tokens) < 4:
                kind = "voltage" if prefix == "V" else "current"
                raise DpspiceError(
                    f"Malformed source line '{s}': {kind} source {name} needs "
                    f"'<node+> <node-> <spec>', e.g. {name} in 0 SINE(0 1 50) "
                    f"or {name} in 0 DC 5."
                )


def _require_tran(netlist, mode_sel: str) -> None:
    """Transient modes (IDP / TD) need a ``.tran`` window; HB does not."""
    if mode_sel in ("idp", "td") and netlist.tran_params() is None:
        raise DpspiceError(
            "No .tran card: the IDP/TD transient modes need a simulation "
            "window. Add `.tran <tstep> <tstop>` (e.g. `.tran 0 0.02`). "
            "Nonlinear circuits can instead run a steady-state harmonic-balance "
            "solve with `--mode hb`."
        )


def _check_size(n_states: int) -> None:
    cap = _max_states()
    if n_states > cap:
        raise DpspiceError(
            f"Circuit has {n_states} MNA states, above the dense-backend limit "
            f"of {cap}. The pure-Python backend factors dense matrices "
            f"(O(n^3) time, O(n^2) memory) and would risk running out of "
            f"memory. Raise the ceiling with DPSPICE_MAX_STATES if your machine "
            f"can handle it, or reduce the circuit size."
        )


# ----------------------------------------------------------------------
# Tier 2 estimators
# ----------------------------------------------------------------------

def _nonlinear_devices(netlist) -> List:
    return [e for e in netlist.elements if e.prefix in _NONLINEAR_PREFIXES]


def _sine_sources(netlist):
    out = []
    for e in netlist.voltage_sources() + netlist.current_sources():
        if e.source_spec and e.source_spec.source_type == SourceType.SINE:
            out.append(e)
    return out


def _extract_carrier_hz(netlist, decisions: List[Decision],
                        warnings: List[str], override_hz: Optional[float]) -> Optional[float]:
    """Tier 2: carrier frequency in Hz, with override + ambiguity handling."""
    if override_hz is not None:
        decisions.append(Decision("omega", override_hz, "override",
                                   f"carrier set by --omega = {override_hz:g} Hz"))
        return override_hz

    sines = _sine_sources(netlist)
    freqs = sorted({round(e.source_spec.sine_freq, 9) for e in sines})
    if not freqs:
        return None
    if len(freqs) == 1:
        f0 = freqs[0]
        decisions.append(Decision("omega", f0, "netlist",
                                   f"single SINE source at {f0:g} Hz"))
        return f0
    # multiple distinct frequencies: pick the largest-amplitude source, warn.
    dominant = max(sines, key=lambda e: abs(e.source_spec.sine_amplitude))
    f0 = dominant.source_spec.sine_freq
    warnings.append(
        f"Multiple SINE frequencies {freqs} Hz detected; picked dominant "
        f"{dominant.name} at {f0:g} Hz. Pass --omega to disambiguate."
    )
    decisions.append(Decision("omega", f0, "auto",
                              f"ambiguous; chose dominant source {dominant.name}"))
    return f0


def _decide_mode(netlist, requested: str,
                 decisions: List[Decision]) -> Tuple[str, str]:
    """Return (mode, reason). ``requested`` is one of auto/idp/td/hb."""
    nl_devs = _nonlinear_devices(netlist)
    if requested != "auto":
        reason = f"mode forced to '{requested}' by --mode"
        decisions.append(Decision("mode", requested, "override", reason))
        return requested, reason

    if nl_devs:
        names = ", ".join(d.name for d in nl_devs)
        reason = f"nonlinear device detected ({names}) -> harmonic-balance"
        decisions.append(Decision("mode", "hb", "auto", reason))
        return "hb", reason

    reason = "linear circuit, no nonlinear devices -> IDP single-shift transient"
    decisions.append(Decision("mode", "idp", "auto", reason))
    return "idp", reason


def _parse_diode_model(netlist, model_name: str) -> dict:
    """Pull Is / N out of a ``.model <name> D(Is=.. N=..)`` card."""
    raw = netlist.models.get(model_name, "")
    params = {}
    for key, attr in (("IS", "Is"), ("N", "n")):
        m = re.search(rf"\b{key}\s*=\s*([0-9.eE+\-]+)", raw, re.IGNORECASE)
        if m:
            params[attr] = float(m.group(1))
    return params


# ----------------------------------------------------------------------
# info (dry run)
# ----------------------------------------------------------------------

def analyze(source: str, mode: str = "auto",
            omega_hz: Optional[float] = None) -> InfoResult:
    """Parse + apply Tier-2 heuristics. No solve. Powers ``dpspice info``."""
    netlist_str = read_netlist(source)
    netlist = parse_ltspice_netlist(netlist_str)

    if not netlist.elements:
        raise DpspiceError("Empty or unparseable netlist (no circuit elements found).")
    _precheck_netlist(netlist_str, netlist)

    decisions: List[Decision] = []
    warnings: List[str] = []

    mode_sel, reason = _decide_mode(netlist, mode, decisions)
    f0 = _extract_carrier_hz(netlist, decisions, warnings, omega_hz)

    try:
        mna = build_mna(netlist)
        n_states = int(mna.n_total)
    except Exception as exc:  # pragma: no cover - defensive
        raise DpspiceError(f"Could not build MNA system: {exc}") from exc
    _check_size(n_states)

    devices = [f"{e.name} ({e.prefix})" for e in netlist.elements]
    has_nl = bool(_nonlinear_devices(netlist))
    tran = netlist.tran_params()

    if mode_sel in ("idp", "td") and f0 is None:
        warnings.append("No SINE/PULSE carrier found; IDP/TD needs --omega for the phasor solve.")

    return InfoResult(
        netlist_title=netlist.title or "(untitled)",
        n_states=n_states,
        n_nodes=len(netlist.non_ground_nodes()),
        mode_selected=mode_sel,
        reason=reason,
        omega_hz=f0,
        devices=devices,
        has_nonlinear=has_nl,
        tran=tran,
        decisions=decisions,
        warnings=warnings,
    )


# ----------------------------------------------------------------------
# run
# ----------------------------------------------------------------------

def _tran_span(netlist) -> Optional[Tuple[float, float]]:
    tp = netlist.tran_params()
    if not tp or "t_stop" not in tp:
        return None
    return (float(tp.get("t_start", 0.0)), float(tp["t_stop"]))


def _summarise_nodes(t: np.ndarray, node_voltages: dict) -> dict:
    out = {}
    for name, v in node_voltages.items():
        v = np.real(np.asarray(v))
        out[name] = {
            "final": float(v[-1]),
            "peak": float(np.max(np.abs(v))),
            "rms": float(np.sqrt(np.mean(v ** 2))),
        }
    return out


def _run_linear(netlist_str, netlist, mode, f0, decisions, warnings,
                with_waveforms) -> RunResult:
    if f0 is None and mode in ("idp",):
        raise DpspiceError(
            "IDP mode needs a carrier frequency but none was found in the netlist. "
            "Add a SINE source or pass --omega <Hz>."
        )
    circuit = NetlistCircuit.from_string(netlist_str)
    n_states = int(circuit.mna.n_total)
    span = _tran_span(netlist)

    t0 = time.perf_counter()
    if mode == "td":
        res = circuit.solve_time_domain(t_span=span)
        solver = "td"
    else:  # idp
        circuit.configure_phasor(omega_s=2 * np.pi * f0)
        res = circuit.solve_phasor_domain(t_span=span)
        solver = "idp"
    dt = time.perf_counter() - t0

    t = np.asarray(res["t"], dtype=float)
    # Both solve_time_domain and solve_phasor_domain expose per-node signals as
    # "V(<node>)" keys; solve_time_domain additionally has a node_voltages dict,
    # solve_phasor_domain does not. Read the V(...) keys for a uniform path.
    node_v = {k[2:-1]: res[k] for k in res
              if k.startswith("V(") and k.endswith(")")}
    waveforms = []
    if with_waveforms:
        for name, v in node_v.items():
            waveforms.append(Waveform.from_arrays(f"V({name})", t, v))

    return RunResult(
        netlist_title=netlist.title or "(untitled)",
        solver=solver,
        mode_selected=mode,
        reason=next((d.reason for d in decisions if d.field == "mode"), ""),
        omega_hz=f0,
        n_states=n_states,
        decisions=decisions,
        solve_time_s=dt,
        summary={"nodes": _summarise_nodes(t, node_v),
                 "t_end": float(t[-1]) if t.size else 0.0,
                 "n_timepoints": int(t.size)},
        waveforms=waveforms,
        warnings=warnings,
    )


def solve_hb_native(netlist_str, netlist, f0, harmonics, tol,
                    warnings=None, decisions=None):
    """Build the rectifier HB system and solve it, returning the native engine
    objects ``(HBResult, diode_objs, diodes_nl, elapsed_s)``.

    Shared by :func:`run` and :mod:`dpspice.validate` so both go through the
    exact same solve path.
    """
    from mna import HBNet                       # noqa: E402  rectifier/mna.py
    from device import ShockleyDiode            # noqa: E402
    from reference_td import Diode              # noqa: E402
    import hb_solver as hb                       # noqa: E402

    warnings = warnings if warnings is not None else []
    diodes_nl = _nonlinear_devices(netlist)
    unsupported = [d for d in diodes_nl if d.prefix != "D"]
    if unsupported:
        names = ", ".join(f"{d.name} ({d.prefix})" for d in unsupported)
        raise DpspiceError(
            f"Harmonic-balance v1 supports diodes only; unsupported nonlinear "
            f"device(s): {names}. MOSFET/BJT models are not implemented yet."
        )
    if not diodes_nl:
        raise DpspiceError("Harmonic-balance mode requested but no diode found in the netlist.")
    if f0 is None:
        raise DpspiceError("Harmonic-balance needs a carrier frequency; pass --omega <Hz>.")

    hbnet = HBNet(netlist_str)

    diode_objs = []
    for d in diodes_nl:
        anode, cathode = d.nodes[0], d.nodes[1]
        params = _parse_diode_model(netlist, d.model)
        law = ShockleyDiode(**params) if params else ShockleyDiode()
        diode_objs.append(Diode(hbnet, anode, cathode, law))
    diode_arg = diode_objs[0] if len(diode_objs) == 1 else diode_objs

    # Tier 2: choose K, then adapt upward until converged (announce the choice).
    if harmonics is not None:
        ladder = [harmonics]
        if decisions is not None:
            decisions.append(Decision("harmonics", harmonics, "override",
                                      f"K set by --harmonics = {harmonics}"))
    else:
        ladder = K_LADDER
        if decisions is not None:
            decisions.append(Decision("harmonics", DEFAULT_K, "auto",
                                      f"default K={DEFAULT_K}, auto-raise up to {K_LADDER[-1]} if not converged"))

    t0 = time.perf_counter()
    result = None
    for K in ladder:
        result = hb.solve_newton(hbnet, diode_arg, f0, K=K, tol=tol or 1e-10)
        if result.converged:
            if K != ladder[0]:
                warnings.append(f"Raised K to {K} to reach convergence.")
            break
    elapsed = time.perf_counter() - t0
    if result is None or not result.converged:
        raise DpspiceError(
            f"Harmonic-balance did not converge up to K={ladder[-1]} "
            f"(residual {getattr(result, 'residual', float('nan')):.2e}). "
            f"Increase --harmonics or --tol."
        )
    return result, diode_objs, diodes_nl, elapsed


def _run_hb(netlist_str, netlist, f0, harmonics, tol, decisions, warnings,
            with_waveforms) -> RunResult:
    import metrics as M                          # noqa: E402

    result, diode_objs, diodes_nl, dt = solve_hb_native(
        netlist_str, netlist, f0, harmonics, tol, warnings, decisions)
    n_states = int(result.hbnet.n)

    # Summaries: per-node dc + ripple; conduction angle for a single diode.
    nodes = netlist.non_ground_nodes()
    node_summary = {}
    waveforms = []
    for node in nodes:
        if result.hbnet.idx(node) < 0:
            continue
        tt, vv = result.waveform(node)
        node_summary[f"V({node})"] = {
            "vdc": float(M.vdc(vv)),
            "ripple": float(M.ripple(vv)),
            "peak": float(np.max(np.abs(vv))),
        }
        if with_waveforms:
            waveforms.append(Waveform.from_arrays(f"V({node})", tt, vv))

    summary = {"nodes": node_summary, "n_harmonics": int(result.K)}
    if len(diode_objs) == 1:
        d = diodes_nl[0]
        ta, va = result.waveform(d.nodes[0])
        tc, vc = result.waveform(d.nodes[1])
        law = diode_objs[0].law
        if hasattr(law, "I"):
            i_d = np.asarray(law.I(va - vc), dtype=float)
            summary["conduction_angle_deg"] = float(M.conduction_angle(i_d))

    return RunResult(
        netlist_title=netlist.title or "(untitled)",
        solver="hb",
        mode_selected="hb",
        reason=next((d.reason for d in decisions if d.field == "mode"), ""),
        omega_hz=f0,
        n_states=n_states,
        K=int(result.K),
        converged=bool(result.converged),
        iters=int(result.iters),
        residual=float(result.residual),
        decisions=decisions,
        solve_time_s=dt,
        summary=summary,
        waveforms=waveforms,
        warnings=warnings,
    )


def run(source: str, mode: str = "auto", harmonics: Optional[int] = None,
        omega_hz: Optional[float] = None, tol: Optional[float] = None,
        with_waveforms: bool = True) -> RunResult:
    """Parse, auto-decide, simulate. The one-call entry point."""
    netlist_str = read_netlist(source)
    netlist = parse_ltspice_netlist(netlist_str)
    if not netlist.elements:
        raise DpspiceError("Empty or unparseable netlist (no circuit elements found).")
    _precheck_netlist(netlist_str, netlist)

    decisions: List[Decision] = []
    warnings: List[str] = []
    mode_sel, _ = _decide_mode(netlist, mode, decisions)
    f0 = _extract_carrier_hz(netlist, decisions, warnings, omega_hz)

    # The transient modes need a simulation window; reject early with a clear
    # message instead of letting the engine raise a bare ValueError mid-solve.
    _require_tran(netlist, mode_sel)

    try:
        n_states = int(build_mna(netlist).n_total)
    except DpspiceError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise DpspiceError(f"Could not build MNA system: {exc}") from exc
    _check_size(n_states)

    if mode_sel == "hb":
        return _run_hb(netlist_str, netlist, f0, harmonics, tol,
                       decisions, warnings, with_waveforms)
    return _run_linear(netlist_str, netlist, mode_sel, f0,
                       decisions, warnings, with_waveforms)
