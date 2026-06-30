"""Shared waveform-comparison metrics.

Both ``dpspice validate`` (single-circuit, interactive) and the batch
validation suite compute their numbers here, so the definitions are identical
everywhere and match the paper. NRMSE is RMSE normalised by the reference
peak-to-peak, exactly as in the engine's ``metrics`` module.
"""
from __future__ import annotations

from typing import Dict

import numpy as np

# Engine metrics (sys.path wired by importing dispatch -> _engine elsewhere).
import metrics as _M  # noqa: E402


def r2(ref: np.ndarray, test: np.ndarray) -> float:
    """Coefficient of determination of ``test`` against ``ref``."""
    ref = np.asarray(ref, dtype=float)
    test = np.asarray(test, dtype=float)
    ss_tot = np.sum((ref - np.mean(ref)) ** 2)
    ss_res = np.sum((ref - test) ** 2)
    return float(1.0 - ss_res / ss_tot) if ss_tot > 0 else float("nan")


def compare_on_time_grid(t_ref: np.ndarray, v_ref: np.ndarray,
                         t_test: np.ndarray, v_test: np.ndarray,
                         grid_points: int = 2000) -> Dict[str, float]:
    """Interpolate both signals onto a shared time grid over their overlap and
    return NRMSE / R^2 / max-abs-error / DC match.

    Used for transient (IDP/TD) comparisons where the full waveform — startup
    transient included — must line up in absolute time.
    """
    t_ref = np.asarray(t_ref, dtype=float)
    v_ref = np.real(np.asarray(v_ref))
    t_test = np.asarray(t_test, dtype=float)
    v_test = np.real(np.asarray(v_test))

    lo = max(t_ref.min(), t_test.min())
    hi = min(t_ref.max(), t_test.max())
    if not (hi > lo):
        raise ValueError("reference and test waveforms do not overlap in time")
    grid = np.linspace(lo, hi, grid_points)
    vr = np.interp(grid, t_ref, v_ref)
    vt = np.interp(grid, t_test, v_test)
    return {
        "nrmse": float(_M.nrmse(vr, vt)),
        "r2": r2(vr, vt),
        "max_abs_error": float(np.max(np.abs(vt - vr))),
        "dc_ref": float(np.mean(vr)),
        "dc_test": float(np.mean(vt)),
    }


def compare_on_phase_grid(ph: np.ndarray, v_ref: np.ndarray,
                          v_test: np.ndarray) -> Dict[str, float]:
    """Metrics for two signals already resampled onto a common phase grid.

    Used for the harmonic-balance path, where the solver returns one steady
    period and the reference is folded onto the same period.
    """
    v_ref = np.real(np.asarray(v_ref))
    v_test = np.real(np.asarray(v_test))
    return {
        "nrmse": float(_M.nrmse(v_ref, v_test)),
        "r2": r2(v_ref, v_test),
        "max_abs_error": float(np.max(np.abs(v_test - v_ref))),
        "dc_ref": float(np.mean(v_ref)),
        "dc_test": float(np.mean(v_test)),
    }
