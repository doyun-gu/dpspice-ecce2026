"""Structured, JSON-serialisable results for the DPSpice CLI / MCP server.

The engine returns numpy arrays and bespoke result objects. Everything that
crosses the CLI/MCP boundary is normalised into the dataclasses here so it can
be printed as a table, written to ``result.json``, or returned to an MCP
client without leaking numpy types.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

import numpy as np


def _f(x: Any) -> Any:
    """Coerce numpy scalars/arrays into JSON-friendly Python types."""
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    if isinstance(x, np.ndarray):
        return [_f(v) for v in x.tolist()]
    if isinstance(x, complex):
        return {"re": x.real, "im": x.imag}
    return x


@dataclass
class Decision:
    """A single Tier-2 auto-decision, surfaced so the user sees what was inferred."""
    field: str            # "mode", "omega", "harmonics", ...
    value: Any
    source: str           # "auto", "override", "netlist"
    reason: str           # human-readable explanation


@dataclass
class Waveform:
    """A named signal sampled on a time grid."""
    name: str
    t: List[float]
    v: List[float]

    @classmethod
    def from_arrays(cls, name: str, t: np.ndarray, v: np.ndarray,
                    max_points: int = 2000) -> "Waveform":
        t = np.asarray(t, dtype=float)
        v = np.real(np.asarray(v))
        if t.size > max_points:
            idx = np.linspace(0, t.size - 1, max_points).astype(int)
            t, v = t[idx], v[idx]
        return cls(name=name, t=t.tolist(), v=v.tolist())


@dataclass
class RunResult:
    """The complete outcome of ``dpspice run`` / ``dpspice_run``."""
    netlist_title: str
    solver: str                       # "idp" | "td" | "hb"
    mode_selected: str
    reason: str
    omega_hz: Optional[float]
    n_states: int
    decisions: List[Decision] = field(default_factory=list)
    # HB-specific
    K: Optional[int] = None
    converged: Optional[bool] = None
    iters: Optional[int] = None
    residual: Optional[float] = None
    # timing
    solve_time_s: float = 0.0
    # scalar summaries the agent can reason over without the full array
    summary: Dict[str, Any] = field(default_factory=dict)
    # optional full/decimated waveforms
    waveforms: List[Waveform] = field(default_factory=list)
    # validation (filled by dpspice validate)
    validation: Optional[Dict[str, Any]] = None
    warnings: List[str] = field(default_factory=list)

    def to_dict(self, include_waveforms: bool = True) -> Dict[str, Any]:
        d = asdict(self)
        d = {k: _f(v) if not isinstance(v, (list, dict)) else v
             for k, v in d.items()}
        d["decisions"] = [asdict(x) for x in self.decisions]
        d["summary"] = {k: _f(v) for k, v in self.summary.items()}
        if include_waveforms:
            d["waveforms"] = [asdict(w) for w in self.waveforms]
        else:
            d["waveforms"] = []
            d["waveform_names"] = [w.name for w in self.waveforms]
        return d


@dataclass
class InfoResult:
    """The outcome of ``dpspice info`` — parse + Tier-2 decisions, no solve."""
    netlist_title: str
    n_states: int
    n_nodes: int
    mode_selected: str
    reason: str
    omega_hz: Optional[float]
    devices: List[str]
    has_nonlinear: bool
    tran: Optional[Dict[str, Any]]
    decisions: List[Decision] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["decisions"] = [asdict(x) for x in self.decisions]
        d["tran"] = {k: _f(v) for k, v in (self.tran or {}).items()} or None
        d["omega_hz"] = _f(self.omega_hz)
        return d
