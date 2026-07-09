"""Switched-linear pipeline: routing, LTP steady state, envelope, hybrid.

Promoted from the validated ``exploration/switched-linear`` prototype. The
routing layer decides, per element, which solution path a switched netlist
needs; the solver layers reuse the package's existing HB assembly (HBNet,
aft) and Newton machinery rather than duplicating them.
"""
from .router import (
    GatedSwitch,
    RoutedDiode,
    RouteRecord,
    RoutingError,
    RoutingTable,
    route_netlist,
)
from .ltp import SwitchedHBNet, solve_ltp, solve_ltp_richardson
from .envelope import assemble_bank, integrate, reconstruct
from .hybrid import solve_hybrid, warm_start

__all__ = [
    "GatedSwitch",
    "RoutedDiode",
    "RouteRecord",
    "RoutingError",
    "RoutingTable",
    "route_netlist",
    "SwitchedHBNet",
    "solve_ltp",
    "solve_ltp_richardson",
    "assemble_bank",
    "integrate",
    "reconstruct",
    "solve_hybrid",
    "warm_start",
]
