"""Vendored DPSpice solver source.

This subpackage holds the real DPSpice engine modules, copied verbatim from
the research repository so the public release is self-contained and every
result flows through the actual solver (no re-implementation, no mocks).

The engine modules use flat absolute imports among themselves
(``from netlist_parser import ...``, ``from mna import HBNet``). Rather than
rewrite that source, we put the engine directory (and the rectifier
sub-directory) on ``sys.path`` at import time so those imports resolve to the
vendored copies and nothing else.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_RECTIFIER = os.path.join(_HERE, "rectifier")

for _p in (_HERE, _RECTIFIER):
    if _p not in sys.path:
        sys.path.insert(0, _p)

del os, sys
