"""DPSpice MCP server (stdio).

Exposes the auto-run engine to any MCP client (Claude Code, Claude Desktop,
etc.) as a small set of tools. Design rules for this boundary:

* Netlists arrive as **strings** (clients paste text, they do not share files).
* Every return value is **plain JSON** — no numpy types, no engine objects.
* **No banners, spinners, or decorative output** ever reach a tool result;
  that chrome lives only in the CLI. Tool results are data.
* **Tool results stay bounded.** A solve can produce thousands of waveform
  samples per node; returning those inline would bloat every tool result and
  flood the agent's context. So ``dpspice_run`` returns scalar summaries plus a
  compact per-waveform *descriptor* and a **handle**; the full arrays are
  fetched on demand, decimated, via ``dpspice_waveforms``.
* **stdout is reserved for the JSON-RPC stream.** Over stdio, anything written
  to stdout that is not protocol corrupts the channel, so all logging is pinned
  to stderr (see :func:`main`).
* Errors are returned as concise messages, never raw tracebacks.

Run with ``dpspice-mcp`` (entry point) or ``python -m dpspice.mcp_server``.
"""
from __future__ import annotations

import logging
import sys
from collections import OrderedDict
from typing import Optional

try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError as exc:  # MCP extra not installed
    raise SystemExit(
        f"The `dpspice-mcp` server needs the MCP extra (missing: {exc.name}). "
        f"Install it with:  pip install 'dpspice[mcp]'"
    )

from . import __version__
from . import api
from .dispatch import DpspiceError

log = logging.getLogger("dpspice.mcp")

mcp = FastMCP("dpspice")


# ----------------------------------------------------------------------
# Bounded waveform store (summary + handle pattern)
#
# A run that the client asks for waveforms on parks its arrays here under a
# short handle and returns only descriptors. The store keeps the most recent
# handful of results (an LRU) so memory stays bounded across many runs; old
# handles expire silently and a fetch on an expired handle returns a clear
# error telling the caller to re-run. Handles use a process-local counter (no
# wall-clock, no RNG) so behaviour is deterministic and testable.
# ----------------------------------------------------------------------

_WAVE_STORE: "OrderedDict[str, list]" = OrderedDict()
_WAVE_STORE_MAX = 16          # keep the last N runs' waveforms
_WAVE_COUNTER = 0


def _store_waveforms(waveforms) -> str:
    """Park a run's waveforms and return a fresh handle."""
    global _WAVE_COUNTER
    _WAVE_COUNTER += 1
    handle = f"wf-{_WAVE_COUNTER}"
    _WAVE_STORE[handle] = list(waveforms)
    _WAVE_STORE.move_to_end(handle)
    while len(_WAVE_STORE) > _WAVE_STORE_MAX:
        _WAVE_STORE.popitem(last=False)   # evict oldest
    return handle


def _waveform_descriptors(waveforms) -> list:
    """A compact, bounded description of each waveform — never the full array."""
    descriptors = []
    for w in waveforms:
        n = len(w.t)
        descriptors.append({
            "name": w.name,
            "points": n,
            "t_start": w.t[0] if n else None,
            "t_end": w.t[-1] if n else None,
            "v_min": min(w.v) if n else None,
            "v_max": max(w.v) if n else None,
        })
    return descriptors


@mcp.tool()
def dpspice_info(netlist: str, mode: str = "auto",
                 omega_hz: Optional[float] = None) -> dict:
    """Parse a SPICE netlist and report what DPSpice would do — without solving.

    Returns the MNA state count, node count, the auto-selected analysis mode
    (idp / td / hb) and why, the detected carrier frequency, the device list,
    and every Tier-2 auto-decision. Use this to preview a run.

    Args:
        netlist: The SPICE netlist text (paste the file contents).
        mode: auto | td | idp | hb. ``auto`` lets DPSpice decide.
        omega_hz: Carrier frequency in Hz to override source detection.
    """
    try:
        return api.load(netlist).info(mode=mode, omega=omega_hz).to_dict()
    except DpspiceError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # never leak a raw traceback to the MCP client
        return {"error": f"unexpected {type(exc).__name__}: {exc}"}


@mcp.tool()
def dpspice_run(netlist: str, mode: str = "auto",
                harmonics: Optional[int] = None,
                omega_hz: Optional[float] = None,
                tol: Optional[float] = None,
                include_waveforms: bool = False) -> dict:
    """Simulate a SPICE netlist and return the result as JSON.

    DPSpice auto-detects the right method: a linear circuit solves with the
    instantaneous-dynamic-phasor (IDP) single-shift transient; a circuit with
    a diode solves with harmonic balance (HB). All auto-decisions are surfaced
    in ``decisions``.

    Args:
        netlist: The SPICE netlist text.
        mode: auto | td | idp | hb.
        harmonics: HB harmonic count K (HB only); auto-chosen if omitted.
        omega_hz: Carrier frequency in Hz; auto-detected from a SINE source if omitted.
        tol: Solver tolerance.
        include_waveforms: If true, compute per-node waveforms and return a
            bounded **descriptor** for each (name, point count, time span, min/max)
            plus a ``waveforms_handle``. The arrays themselves are *not* inlined —
            fetch them on demand (decimated) with ``dpspice_waveforms(handle, ...)``.
            Default false returns only scalar summaries (dc, peak, rms, ripple,
            conduction angle) plus convergence info.
    """
    try:
        result = api.load(netlist).run(mode=mode, harmonics=harmonics,
                                       omega=omega_hz, tol=tol,
                                       with_waveforms=include_waveforms)
        # Always return bounded scalars; never inline the arrays.
        payload = result.to_dict(include_waveforms=False)
        if include_waveforms and result.waveforms:
            handle = _store_waveforms(result.waveforms)
            payload["waveforms_available"] = True
            payload["waveforms_handle"] = handle
            payload["waveforms"] = _waveform_descriptors(result.waveforms)
            payload["waveforms_hint"] = (
                f"Arrays are not inlined. Call dpspice_waveforms(handle='{handle}') "
                f"for all nodes, or name='<node>' for one, with an optional "
                f"max_points cap (default 512)."
            )
        else:
            payload["waveforms_available"] = False
        return payload
    except DpspiceError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # never leak a raw traceback to the MCP client
        return {"error": f"unexpected {type(exc).__name__}: {exc}"}


@mcp.tool()
def dpspice_waveforms(handle: str, name: Optional[str] = None,
                      max_points: int = 512) -> dict:
    """Fetch the waveform arrays parked by a prior ``dpspice_run`` (handle pattern).

    ``dpspice_run`` with ``include_waveforms=true`` returns descriptors and a
    handle rather than the raw arrays. Use this tool to pull the actual samples,
    decimated to a bounded ``max_points`` so a single fetch can never flood the
    context. Request one node with ``name`` to keep the result small.

    Args:
        handle: The ``waveforms_handle`` returned by ``dpspice_run``.
        name: Optional single waveform name (e.g. ``v(out)``); omit for all nodes.
        max_points: Cap on samples returned per waveform (uniform decimation).
            Clamped to [2, 5000]; default 512.
    """
    waveforms = _WAVE_STORE.get(handle)
    if waveforms is None:
        return {"error": f"unknown or expired handle '{handle}'. Re-run "
                         f"dpspice_run with include_waveforms=true to get a fresh one."}
    _WAVE_STORE.move_to_end(handle)   # refresh LRU on use

    cap = max(2, min(int(max_points), 5000))
    if name is not None:
        wanted = [w for w in waveforms if w.name.lower() == name.lower()]
        if not wanted:
            avail = [w.name for w in waveforms]
            return {"error": f"no waveform named '{name}' in handle '{handle}'. "
                             f"Available: {avail}"}
    else:
        wanted = list(waveforms)

    out = []
    for w in wanted:
        n = len(w.t)
        if n > cap:
            # uniform decimation, endpoints preserved
            step = (n - 1) / (cap - 1)
            idx = [int(round(i * step)) for i in range(cap)]
            t = [w.t[i] for i in idx]
            v = [w.v[i] for i in idx]
            decimated = True
        else:
            t, v = list(w.t), list(w.v)
            decimated = False
        out.append({"name": w.name, "points": len(t), "source_points": n,
                    "decimated": decimated, "t": t, "v": v})
    return {"handle": handle, "max_points": cap, "waveforms": out}


@mcp.tool()
def dpspice_validate(netlist: str, ltspice_raw: str, mode: str = "auto",
                     harmonics: Optional[int] = None,
                     omega_hz: Optional[float] = None) -> dict:
    """Cross-validate a DPSpice run against an LTspice ``.raw`` reference.

    Folds both onto one fundamental period and reports NRMSE and R^2 per
    matched node, plus the worst-case NRMSE and minimum R^2.

    Args:
        netlist: The SPICE netlist text.
        ltspice_raw: Filesystem path to the reference LTspice .raw file.
            (The reference is a binary file, so a path is required here.)
        mode: auto | td | idp | hb.
        harmonics: HB harmonic count K.
        omega_hz: Carrier frequency in Hz.
    """
    try:
        return api.load(netlist).validate(ref=ltspice_raw, mode=mode,
                                          harmonics=harmonics,
                                          omega=omega_hz).to_dict()
    except DpspiceError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # never leak a raw traceback to the MCP client
        return {"error": f"unexpected {type(exc).__name__}: {exc}"}


def _route_logs_to_stderr() -> None:
    """Pin every log record to stderr so stdout stays a clean JSON-RPC stream.

    Over stdio transport, stdout carries the MCP protocol; a stray INFO line
    there corrupts the channel and shows up as garbage in a tool result. We
    install a single stderr handler on the root logger and drop any handler
    that targets stdout (some environments pre-configure one).
    """
    root = logging.getLogger()
    for h in list(root.handlers):
        stream = getattr(h, "stream", None)
        if stream is sys.stdout:
            root.removeHandler(h)
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def main() -> None:
    """Entry point: run the server over stdio."""
    _route_logs_to_stderr()
    log.info("dpspice-mcp v%s starting (stdio)", __version__)
    mcp.run()


if __name__ == "__main__":
    main()
