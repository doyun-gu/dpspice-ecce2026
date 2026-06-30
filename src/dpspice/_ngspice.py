"""Drive ngspice as an independent reference oracle.

ngspice is an OPTIONAL system binary (not a pip dependency). When present,
``dpspice validate`` and the validation suite can generate their own reference
``.raw`` instead of requiring the user to run ngspice by hand. When absent,
callers must degrade gracefully (clear message, or fall back to a provided
``--ref``); nothing here may hard-crash the rest of the tool.

The bundled example netlists are written in the LTspice dialect (``SINE(...)``,
a zero ``Tstep`` in ``.tran``, ``.backanno``). ngspice needs small, mechanical
adjustments, applied here without touching the engine's own parser.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from typing import Optional, Tuple

# Engine parser (sys.path wired by importing dispatch -> _engine elsewhere).
from netlist_parser import parse_ltspice_netlist  # noqa: E402


class NgspiceError(Exception):
    """ngspice missing, or it diverged / errored on a netlist."""


def ngspice_path() -> Optional[str]:
    """Return the ngspice executable path, or None if not on PATH."""
    return shutil.which("ngspice")


def ngspice_available() -> bool:
    return ngspice_path() is not None


def _clean_tran_line(netlist) -> str:
    """Build an ngspice-valid ``.tran`` line from the parsed window.

    ngspice requires a positive print step as the first field; LTspice allows
    ``0``. We use the LTspice max-step if given, else a fine fraction of the
    window, and pass it as both the print step and the ngspice ``Tmax`` so the
    reference is resolved at least as finely as the LTspice/DPSpice run.
    """
    tp = netlist.tran_params() or {}
    t_stop = float(tp.get("t_stop", 0.0) or 0.0)
    if t_stop <= 0:
        raise NgspiceError("netlist has no usable .tran stop time for ngspice")
    t_start = float(tp.get("t_start", 0.0) or 0.0)
    t_max = float(tp.get("t_maxstep", 0.0) or 0.0)
    if t_max <= 0:
        t_max = t_stop / 20000.0  # default: ~20k points across the window
    line = f".tran {t_max:g} {t_stop:g}"
    if t_start > 0:
        line += f" {t_start:g} {t_max:g}"
    return line


def to_ngspice_deck(netlist_str: str) -> str:
    """Translate an LTspice-dialect netlist into an ngspice batch deck."""
    netlist = parse_ltspice_netlist(netlist_str)

    lines = netlist_str.splitlines()
    title = lines[0] if lines and not lines[0].strip().lower().startswith(
        (".",)) else "dpspice ngspice reference"

    out = [title.lstrip("* ").strip() or "dpspice ngspice reference"]
    saw_tran = False
    for raw in lines[1:]:
        s = raw.strip()
        low = s.lower()
        if not s:
            continue
        if low.startswith(".end"):
            continue
        if low.startswith(".backanno"):
            continue
        if low.startswith(".tran"):
            out.append(_clean_tran_line(netlist))
            saw_tran = True
            continue
        # LTspice SINE(...) -> ngspice SIN(...)
        s = re.sub(r"\bSINE\s*\(", "SIN(", s, flags=re.IGNORECASE)
        out.append(s)

    if not saw_tran:
        out.append(_clean_tran_line(netlist))
    out.append(".end")
    return "\n".join(out) + "\n"


def run_ngspice(netlist_str: str, keep: bool = False,
                workdir: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Run ngspice in batch mode, returning ``(raw_path, deck_path_if_kept)``.

    Raises :class:`NgspiceError` if ngspice is missing or the run fails / the
    raw file is not produced (e.g. the circuit diverged in ngspice).
    """
    exe = ngspice_path()
    if exe is None:
        raise NgspiceError(
            "ngspice not found on PATH. Install it (macOS: `brew install "
            "ngspice`; Debian/Ubuntu: `apt install ngspice`) or pass a "
            "reference .raw via --ref."
        )

    deck = to_ngspice_deck(netlist_str)
    tmp = workdir or tempfile.mkdtemp(prefix="dpspice_ng_")
    deck_path = os.path.join(tmp, "circuit.cir")
    raw_path = os.path.join(tmp, "out.raw")
    with open(deck_path, "w", encoding="utf-8") as fh:
        fh.write(deck)

    try:
        proc = subprocess.run(
            [exe, "-b", "-r", raw_path, deck_path],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        raise NgspiceError("ngspice timed out (circuit too large or stiff)") from exc

    if not os.path.isfile(raw_path) or os.path.getsize(raw_path) == 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        raise NgspiceError(
            "ngspice did not produce a .raw (it may have diverged on this "
            "circuit). Last output:\n  " + "\n  ".join(tail)
        )
    return raw_path, (deck_path if keep else None)
