"""Netlist routing for switched circuits.

Every element of a netlist containing ``S`` (voltage-controlled switch)
elements is assigned to exactly one solution path:

* ``linear``      R/L/C (and couplings): stamped into the constant MNA E/A.
* ``source``      independent V/I sources feeding power: RHS b(t) -> B_k.
* ``switch-LTP``  S element whose controlling pair traces to an independent
                  PULSE source: becomes a constant Toeplitz coupling block
                  with (duty, f_sw, phase) read off the PULSE parameters.
* ``diode-NR``    D elements: Newton devices (AFT residual + Jacobian).
* ``gate-drive``  V sources that only pin switch control nodes: consumed by
                  the router, never stamped -- they are commands, not power.
* ``REFUSED``     S element whose gate the router cannot prove is an
                  independent command. Prescribing gate coefficients for a
                  gate the circuit itself decides (a state-dependent gate)
                  silently produces wrong waveforms, so routing fails loudly
                  and points at the Newton/transient path instead.

The routing table is the deliverable: it is exposed programmatically
(:class:`RoutingTable`) and rendered by ``dpspice route``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .. import _engine  # noqa: F401  side-effect: flat engine modules on sys.path

from netlist_parser import (  # noqa: E402
    parse_ltspice_netlist, parse_spice_value, SourceType,
)

_GROUND = {"0", "gnd", "GND", "ground"}

#: Fraction of the switching period above which PULSE rise+fall times are no
#: longer negligible and the two-level gate model stops applying.
_EDGE_FRACTION = 1e-2


class RoutingError(Exception):
    """A netlist cannot be routed to the switched-linear paths."""


@dataclass
class GatedSwitch:
    """A routed switch: constant Toeplitz block data, gate already resolved.

    duty/phase are folded for complement gates (inverted pins or inverted
    PULSE levels), so downstream code never needs to know how the gate was
    written in the netlist.
    """
    name: str
    n_pos: str
    n_neg: str
    duty: float
    phase: float
    f_sw: float
    g_on: float
    g_off: float
    gate_source: str

    def to_dict(self) -> dict:
        return {
            "name": self.name, "n_pos": self.n_pos, "n_neg": self.n_neg,
            "duty": self.duty, "phase": self.phase, "f_sw": self.f_sw,
            "g_on": self.g_on, "g_off": self.g_off,
            "gate_source": self.gate_source,
        }


@dataclass
class RoutedDiode:
    """A diode headed for the Newton path, with its Shockley parameters."""
    name: str
    n_pos: str
    n_neg: str
    model: str
    params: Dict[str, float] = field(default_factory=dict)  # ShockleyDiode kwargs

    def to_dict(self) -> dict:
        return {"name": self.name, "n_pos": self.n_pos, "n_neg": self.n_neg,
                "model": self.model, "params": dict(self.params)}


@dataclass
class RouteRecord:
    element: str
    cls: str
    path: str

    def to_dict(self) -> dict:
        return {"element": self.element, "class": self.cls, "path": self.path}


@dataclass
class RoutingTable:
    rows: List[RouteRecord]
    switches: List[GatedSwitch]
    diodes: List[RoutedDiode]
    refused: List[Tuple[str, str]]
    clean_netlist: str
    f_sw: Optional[float]

    @property
    def ok(self) -> bool:
        return not self.refused

    def require_ltp(self) -> None:
        """Raise unless every S element routed to the LTP path."""
        if self.refused:
            msgs = "; ".join(f"{name}: {reason}" for name, reason in self.refused)
            raise RoutingError(
                f"Netlist is not LTP-routable -- {msgs}. These elements need "
                f"the Newton (--mode hb on a diode formulation) or transient "
                f"(--mode td) path instead of a prescribed-gate solve."
            )

    def to_dict(self) -> dict:
        return {
            "rows": [r.to_dict() for r in self.rows],
            "switches": [s.to_dict() for s in self.switches],
            "diodes": [d.to_dict() for d in self.diodes],
            "refused": [{"element": n, "reason": r} for n, r in self.refused],
            "f_sw": self.f_sw,
            "ok": self.ok,
        }


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

def _logical_lines(netlist_str: str) -> List[str]:
    """Comment-stripped, continuation-joined netlist lines (order kept).

    Mirrors the engine parser's line handling so the reconstructed clean
    netlist re-parses to the same elements.
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


def _parse_model_card(raw: str) -> Tuple[str, Dict[str, float]]:
    """'SW(Ron=1m Roff=1Meg Vt=0.5)' -> ('SW', {'ron': 1e-3, ...})."""
    m = re.match(r"\s*([A-Za-z]+)", raw)
    mtype = m.group(1).upper() if m else ""
    kv: Dict[str, float] = {}
    for key, val in re.findall(r"([A-Za-z]+)\s*=\s*([^\s()]+)", raw):
        try:
            kv[key.lower()] = float(parse_spice_value(val))
        except (ValueError, TypeError):
            pass  # non-numeric model parameters are not used by these paths
    return mtype, kv


def _pulse_gate(spec) -> Optional[tuple]:
    """(v1, v2, td, tr, tf, pw, per) of a PULSE source spec, else None."""
    if spec is None or spec.source_type != SourceType.PULSE:
        return None
    return (spec.pulse_v1, spec.pulse_v2, spec.pulse_delay,
            spec.pulse_rise, spec.pulse_fall, spec.pulse_on, spec.pulse_period)


# ----------------------------------------------------------------------
# the router
# ----------------------------------------------------------------------

def route_netlist(netlist_str: str) -> RoutingTable:
    """Classify every element of a switched netlist and extract LTP gate data."""
    netlist = parse_ltspice_netlist(netlist_str)
    models = {name.lower(): _parse_model_card(raw)
              for name, raw in netlist.models.items()}

    s_elems = [e for e in netlist.elements if e.prefix == "S"]
    d_elems = [e for e in netlist.elements if e.prefix == "D"]
    v_elems = [e for e in netlist.elements if e.prefix == "V"]

    # gate tracing index: controlling node pair -> pinning V source
    vsrc_by_nodes = {(e.nodes[0], e.nodes[1]): e for e in v_elems
                     if len(e.nodes) >= 2}

    # power-path node set: every node an energy-carrying element touches.
    # V sources are deliberately excluded (a gate drive is a V source too);
    # a gate pair pinned by NO source but touching these nodes is
    # state-dependent, which the LTP model cannot represent.
    power_nodes = set()
    for e in netlist.elements:
        if e.prefix in ("R", "L", "C", "I", "T"):
            power_nodes.update(e.nodes[:2])
    for e in s_elems + d_elems:
        power_nodes.update(e.nodes[:2])

    rows: List[RouteRecord] = []
    switches: List[GatedSwitch] = []
    diodes: List[RoutedDiode] = []
    refused: List[Tuple[str, str]] = []
    gate_srcs = set()

    for e in s_elems:
        if len(e.nodes) < 4 or not e.model:
            refused.append((e.name, (
                f"malformed switch line: expected "
                f"'{e.name} n+ n- nc+ nc- MODEL' with a .model ... SW(...) card")))
            continue
        npos, nneg, ncp, ncm = e.nodes[0], e.nodes[1], e.nodes[2], e.nodes[3]
        mtype, mkv = models.get(e.model.lower(), ("", {}))
        if mtype != "SW":
            refused.append((e.name, f"no .model {e.model} SW(...) card found"))
            continue
        g_on = 1.0 / mkv.get("ron", 1.0)
        g_off = 1.0 / mkv.get("roff", 1e12)
        vt = mkv.get("vt", 0.0)

        src = vsrc_by_nodes.get((ncp, ncm))
        inverted_pins = False
        if src is None:
            src = vsrc_by_nodes.get((ncm, ncp))
            inverted_pins = src is not None
        if src is None:
            # Ground is trivially a power node, so only a NON-ground control
            # node coinciding with the power path makes the gate state-
            # dependent. A control node that is neither ground nor pinned by a
            # source is simply floating -- a different, clearer diagnostic.
            ctrl_power = sorted(
                {ncp, ncm}
                & power_nodes
                - {g for g in (ncp, ncm) if g.lower() in _GROUND})
            if ctrl_power:
                reason = (
                    f"controlling node {ctrl_power[0]} is part of the power "
                    f"path -- the gate is state-dependent (the circuit decides "
                    f"the switching instants), which the prescribed-gate LTP "
                    f"model cannot represent; use the Newton or transient path")
            else:
                reason = (f"controlling nodes ({ncp},{ncm}) are not pinned by "
                          f"any independent source")
            refused.append((e.name, reason))
            continue

        pulse = _pulse_gate(src.source_spec)
        if pulse is None:
            kind = (src.source_spec.source_type.value
                    if src.source_spec else "unspecified")
            refused.append((e.name, (
                f"gate source {src.name} is {kind}, not a PULSE train -- "
                f"cannot extract (duty, f_sw, phase); use the Newton or "
                f"transient path")))
            continue
        v1, v2, td, tr, tf, pw, per = pulse
        if inverted_pins:
            v1, v2 = -v1, -v2
        if per <= 0:
            refused.append((e.name, f"gate source {src.name}: PER={per:g} is "
                            f"not periodic"))
            continue
        if (tr + tf) > _EDGE_FRACTION * per:
            refused.append((e.name, (
                f"gate source {src.name}: rise/fall {tr:g}+{tf:g} s is not "
                f"negligible vs PER={per:g} s; the two-level LTP gate model "
                f"does not apply")))
            continue

        f_sw = 1.0 / per
        duty = (pw + 0.5 * (tr + tf)) / per
        phase = ((td + 0.5 * tr) / per) % 1.0
        if v2 > vt >= v1:
            inv = False
        elif v1 > vt >= v2:
            inv = True                # ON outside the pulse window (complement)
        else:
            refused.append((e.name, (
                f"gate source {src.name}: threshold Vt={vt:g} is not crossed "
                f"by the pulse levels ({v1:g},{v2:g}) -- the switch never "
                f"toggles")))
            continue
        if inv:
            duty, phase = 1.0 - duty, (phase + pw / per) % 1.0

        gate_srcs.add(src.name)
        switches.append(GatedSwitch(
            name=e.name, n_pos=npos, n_neg=nneg, duty=duty, phase=phase,
            f_sw=f_sw, g_on=g_on, g_off=g_off, gate_source=src.name))
        rows.append(RouteRecord(e.name, "switch-LTP",
                    f"constant Toeplitz block: d={duty:.6g}, "
                    f"f_sw={f_sw:.6g} Hz, phase={phase:.6g}, "
                    f"g_on={g_on:.3g} S, g_off={g_off:.3g} S"))

    for e in d_elems:
        mtype, mkv = models.get(e.model.lower(), ("", {})) if e.model else ("", {})
        params: Dict[str, float] = {}
        if "is" in mkv:
            params["Is"] = mkv["is"]
        if "n" in mkv:
            params["n"] = mkv["n"]
        diodes.append(RoutedDiode(name=e.name, n_pos=e.nodes[0],
                                  n_neg=e.nodes[1], model=e.model,
                                  params=params))
        rows.append(RouteRecord(e.name, "diode-NR",
                    f"Newton device (Shockley Is={params.get('Is', 1e-14):.3g}, "
                    f"N={params.get('n', 1.0):.3g}); per-iteration AFT "
                    f"residual + Jacobian"))

    # remaining elements + clean netlist reconstruction
    removed = {e.name.lower() for e in s_elems + d_elems}
    removed |= {name.lower() for name in gate_srcs}
    clean_lines: List[str] = []
    for line in _logical_lines(netlist_str):
        first = line.split()[0]
        low = first.lower()
        if low.startswith(".model"):
            continue
        if low == ".end":
            continue
        if not low.startswith(".") and low in removed:
            continue
        clean_lines.append(line)
    clean_netlist = "\n".join(clean_lines + [".end"])

    for e in v_elems:
        if e.name in gate_srcs:
            rows.append(RouteRecord(e.name, "gate-drive",
                        "consumed by the router (pins a switch gate; not stamped)"))
        else:
            rows.append(RouteRecord(e.name, "source",
                        "independent source -> RHS b(t) / B_k"))
    for e in netlist.elements:
        if e.prefix in ("S", "D", "V"):
            continue
        rows.append(RouteRecord(e.name, "linear",
                    "stamped into the constant MNA E/A"))
    for name, reason in refused:
        rows.append(RouteRecord(name, "REFUSED", reason))

    # every routed switch/diode power node must survive into the clean netlist,
    # otherwise the MNA loses the node and the Toeplitz/Newton stamp is void.
    clean_nodes = set()
    for e in parse_ltspice_netlist(clean_netlist).elements:
        if e.prefix != "K":
            clean_nodes.update(e.nodes[:2])
    clean_nodes |= _GROUND
    for s in switches:
        for node in (s.n_pos, s.n_neg):
            if node not in clean_nodes:
                raise RoutingError(
                    f"switch {s.name}: node {node} is touched only by "
                    f"switch/diode elements, so the linear MNA loses it. Add "
                    f"a large shunt resistor (e.g. 1e9 ohm) at {node}.")
    for d in diodes:
        for node in (d.n_pos, d.n_neg):
            if node not in clean_nodes:
                raise RoutingError(
                    f"diode {d.name}: node {node} is touched only by "
                    f"switch/diode elements, so the linear MNA loses it. Add "
                    f"a large shunt resistor (e.g. 1e9 ohm) at {node}.")

    fs = sorted({s.f_sw for s in switches})
    if len(fs) > 1 and (fs[-1] - fs[0]) > 1e-9 * fs[-1]:
        raise RoutingError(f"switch gates disagree on the switching frequency: "
                           f"{fs} Hz. All gated switches must share one f_sw.")

    # present rows in original element order, refusal verdicts inline
    order = {e.name: i for i, e in enumerate(netlist.elements)}
    rows.sort(key=lambda r: order.get(r.element, 10 ** 6))
    return RoutingTable(rows=rows, switches=switches, diodes=diodes,
                        refused=refused, clean_netlist=clean_netlist,
                        f_sw=(fs[0] if fs else None))
