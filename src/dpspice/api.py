"""The stable public Python API: ``import dpspice``.

This is the single core layer that every interface sits on. The CLI and the MCP
server are thin wrappers over the functions and objects here, so both produce
identical results from identical code. A future HTTP service layer (not in this
release) is designed to sit on this same API, for the same reason.

    import dpspice

    ckt = dpspice.load("circuit.sp")     # path OR netlist string
    info = ckt.info()                     # MNA states, mode, omega, devices; no solve
    result = ckt.run(mode="auto")         # simulate -> Result
    result.solve_time, result.states, result.waveforms
    val = ckt.validate(ref="out.raw")     # or ref=None to auto-run ngspice
    val.nrmse, val.r2

Design contract (depended on by downstream services):

* Accepts a netlist as a **file path or a string**.
* Returns **structured, serialisable** objects (``.to_dict()`` everywhere);
  callers never touch raw numpy unless they read ``.waveforms``.
* **Import-safe and side-effect-free**: importing ``dpspice`` prints nothing,
  needs no terminal, and starts no solve. Banners/animations live only in the
  CLI layer.
* The compute backend is hidden behind :func:`backend`; moving the hot path to
  a compiled backend later does not change anything in this module.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from . import dispatch
from . import crossval
from .dispatch import DpspiceError, read_netlist
from .results import InfoResult, RunResult, Waveform

Number = Union[int, float]


def _coerce_omega(omega: Optional[Union[Number, str]]) -> Optional[float]:
    """Accept omega as Hz (number) or a SPICE-suffixed string ('92.3k')."""
    if omega is None:
        return None
    if isinstance(omega, (int, float)):
        return float(omega)
    from netlist_parser import parse_spice_value
    try:
        return float(parse_spice_value(omega))
    except Exception as exc:
        raise DpspiceError(f"Could not parse omega '{omega}' (try e.g. 92.3k or 50).") from exc


# ----------------------------------------------------------------------
# Result wrappers
# ----------------------------------------------------------------------

class Result:
    """Outcome of a solve. Thin, stable view over the internal RunResult."""

    def __init__(self, run: RunResult, validation: "Optional[Validation]" = None):
        self._run = run
        self.validation = validation

    @property
    def solver(self) -> str:
        return self._run.solver

    @property
    def mode(self) -> str:
        return self._run.mode_selected

    @property
    def reason(self) -> str:
        return self._run.reason

    @property
    def states(self) -> int:
        """MNA state count."""
        return self._run.n_states

    @property
    def solve_time(self) -> float:
        """Wall-clock solve time in seconds."""
        return self._run.solve_time_s

    @property
    def omega_hz(self) -> Optional[float]:
        return self._run.omega_hz

    @property
    def converged(self) -> Optional[bool]:
        return self._run.converged

    @property
    def K(self) -> Optional[int]:
        return self._run.K

    @property
    def iters(self) -> Optional[int]:
        return self._run.iters

    @property
    def residual(self) -> Optional[float]:
        return self._run.residual

    @property
    def summary(self) -> Dict[str, Any]:
        return self._run.summary

    @property
    def waveforms(self) -> List[Waveform]:
        """Per-node waveforms (decimated). Each has ``.name``, ``.t``, ``.v``."""
        return self._run.waveforms

    @property
    def envelopes(self) -> Optional[List[Waveform]]:
        """Phasor-magnitude envelopes |X(t)| (``with_envelopes=True``, IDP only)."""
        return self._run.envelopes

    @property
    def decisions(self):
        return self._run.decisions

    @property
    def warnings(self) -> List[str]:
        return self._run.warnings

    @property
    def nrmse(self) -> Optional[float]:
        """Worst-node NRMSE, if a validation has been attached; else None."""
        return self.validation.worst_nrmse if self.validation else None

    def to_dict(self, include_waveforms: bool = False) -> Dict[str, Any]:
        d = self._run.to_dict(include_waveforms=include_waveforms)
        if self.validation is not None:
            d["validation"] = self.validation.to_dict()
        return d


class Validation:
    """Outcome of a cross-validation against an independent reference."""

    def __init__(self, report: Dict[str, Any]):
        self._r = report

    @property
    def per_node(self) -> List[Dict[str, Any]]:
        return self._r["per_node"]

    @property
    def worst_nrmse(self) -> float:
        return self._r["worst_nrmse"]

    @property
    def min_r2(self) -> float:
        return self._r["min_r2"]

    @property
    def nrmse(self) -> float:
        return self._r["worst_nrmse"]

    @property
    def r2(self) -> float:
        return self._r["min_r2"]

    @property
    def reference_engine(self) -> str:
        return self._r.get("reference_engine", "user")

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._r)


# ----------------------------------------------------------------------
# Circuit
# ----------------------------------------------------------------------

class Circuit:
    """A parsed-on-demand SPICE circuit. Created via :func:`load`."""

    def __init__(self, netlist: str, label: Optional[str] = None):
        # ``netlist`` is the raw text; ``label`` is a friendly name (the path).
        self.netlist = netlist
        self.label = label or "(netlist string)"

    # -- introspection ---------------------------------------------------
    def info(self, mode: str = "auto",
             omega: Optional[Union[Number, str]] = None) -> InfoResult:
        """Parse and apply the Tier-2 heuristics. No solve."""
        return dispatch.analyze(self.netlist, mode=mode,
                                omega_hz=_coerce_omega(omega))

    # -- solve -----------------------------------------------------------
    def run(self, mode: str = "auto", harmonics: Optional[int] = None,
            omega: Optional[Union[Number, str]] = None,
            tol: Optional[float] = None,
            with_waveforms: bool = True,
            with_envelopes: bool = False) -> Result:
        """Auto-decide and simulate. Returns a :class:`Result`."""
        run = dispatch.run(self.netlist, mode=mode, harmonics=harmonics,
                           omega_hz=_coerce_omega(omega), tol=tol,
                           with_waveforms=with_waveforms,
                           with_envelopes=with_envelopes)
        return Result(run)

    # -- cross-validate --------------------------------------------------
    def validate(self, ref: Optional[str] = None, mode: str = "auto",
                 harmonics: Optional[int] = None,
                 omega: Optional[Union[Number, str]] = None,
                 tol: Optional[float] = None,
                 keep_raw: bool = False) -> Validation:
        """Run + cross-validate. ``ref=None`` auto-runs ngspice as the oracle."""
        report = crossval.validate(self.netlist, ref=ref, mode=mode,
                                   harmonics=harmonics,
                                   omega_hz=_coerce_omega(omega), tol=tol,
                                   keep_raw=keep_raw)
        return Validation(report)


# ----------------------------------------------------------------------
# Module-level entry points
# ----------------------------------------------------------------------

def load(source: str) -> Circuit:
    """Load a circuit from a netlist file path or a netlist string."""
    text = read_netlist(source)
    label = source if text is not source and "\n" not in source else None
    return Circuit(text, label=label)


def info(source: str, **kwargs) -> InfoResult:
    """Convenience: ``load(source).info(**kwargs)``."""
    return load(source).info(**kwargs)


def run(source: str, **kwargs) -> Result:
    """Convenience: ``load(source).run(**kwargs)``."""
    return load(source).run(**kwargs)


def validate(source: str, ref: Optional[str] = None, **kwargs) -> Validation:
    """Convenience: ``load(source).validate(ref, **kwargs)``."""
    return load(source).validate(ref=ref, **kwargs)


def backend() -> str:
    """Return the active compute backend name.

    Only the pure-Python backend ships today. A compiled (pybind11/cffi)
    backend can be added later behind this same call without changing any
    caller; this function is the single place that reports which is active.
    """
    return "python"
