"""MCP boundary tests: bounded payloads, the summary+handle pattern, no inline arrays.

A solve can emit thousands of samples per node. The MCP tools must never inline
those arrays into a tool result: ``dpspice_run`` returns scalar summaries plus a
compact descriptor and a handle, and ``dpspice_waveforms`` returns the arrays on
demand, decimated to a bounded cap.
"""
from __future__ import annotations

import json

import pytest

mcp_server = pytest.importorskip("dpspice.mcp_server")
from dpspice.examples import example_text  # noqa: E402


def _rectifier():
    return example_text("rectifier_halfwave.sp")


def test_run_without_waveforms_has_no_arrays():
    res = mcp_server.dpspice_run(_rectifier(), include_waveforms=False)
    assert "error" not in res, res
    assert res["waveforms"] == []
    assert res.get("waveforms_available") is False
    assert "waveforms_handle" not in res


def test_run_with_waveforms_returns_descriptors_and_handle_not_arrays():
    res = mcp_server.dpspice_run(_rectifier(), include_waveforms=True)
    assert "error" not in res, res
    assert res["waveforms_available"] is True
    handle = res["waveforms_handle"]
    assert handle.startswith("wf-")
    # The "waveforms" field must be DESCRIPTORS, not raw samples.
    for d in res["waveforms"]:
        assert set(d) >= {"name", "points", "t_start", "t_end", "v_min", "v_max"}
        assert "t" not in d and "v" not in d, "arrays must not be inlined in run()"
    # And the whole result must be small even though the solve is large.
    assert len(json.dumps(res)) < 4000, "run() result should stay bounded"


def test_waveforms_fetch_by_handle_is_capped():
    res = mcp_server.dpspice_run(_rectifier(), include_waveforms=True)
    handle = res["waveforms_handle"]
    fetched = mcp_server.dpspice_waveforms(handle, max_points=64)
    assert "error" not in fetched, fetched
    assert fetched["max_points"] == 64
    for w in fetched["waveforms"]:
        assert w["points"] <= 64
        assert len(w["t"]) == w["points"] == len(w["v"])
        # endpoints preserved under decimation
        assert isinstance(w["t"][0], float) and isinstance(w["v"][-1], float)


def test_waveforms_fetch_single_node():
    res = mcp_server.dpspice_run(_rectifier(), include_waveforms=True)
    handle = res["waveforms_handle"]
    name = res["waveforms"][0]["name"]
    one = mcp_server.dpspice_waveforms(handle, name=name, max_points=32)
    assert "error" not in one, one
    assert len(one["waveforms"]) == 1
    assert one["waveforms"][0]["name"] == name


def test_waveforms_unknown_handle_errors_cleanly():
    out = mcp_server.dpspice_waveforms("wf-does-not-exist")
    assert "error" in out
    assert "expired" in out["error"] or "unknown" in out["error"]


def test_waveform_store_is_bounded_lru():
    # Many runs must not grow the store without bound.
    handles = [mcp_server.dpspice_run(_rectifier(), include_waveforms=True)["waveforms_handle"]
               for _ in range(mcp_server._WAVE_STORE_MAX + 5)]
    assert len(mcp_server._WAVE_STORE) <= mcp_server._WAVE_STORE_MAX
    # the oldest handles have been evicted
    assert mcp_server.dpspice_waveforms(handles[0]).get("error")
    # the most recent handle still resolves
    assert "error" not in mcp_server.dpspice_waveforms(handles[-1])
