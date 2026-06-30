"""Automated validation suite for DPSpice.

Generator families pair structured circuits with independent oracles
(closed-form analytic for first/second-order linear circuits; ngspice for
everything else), and the suite runner scores every generated circuit against
its oracle on a per-family tolerance band. Every number comes from a real
DPSpice solve compared to a real reference — nothing is hard-coded.

Public surface:

* :func:`run_suite` — run all (or selected) families, return a JSON-able report.
* :func:`run_case` / :data:`families.all_cases` — single-case and case listing.
* :func:`self_check` — confirm the two oracles agree before trusting the suite.
"""
from __future__ import annotations

from . import families, oracles
from .families import Case, Family, FAMILIES, all_cases
from .suite import run_suite, run_case, self_check

__all__ = [
    "families", "oracles",
    "Case", "Family", "FAMILIES", "all_cases",
    "run_suite", "run_case", "self_check",
]
