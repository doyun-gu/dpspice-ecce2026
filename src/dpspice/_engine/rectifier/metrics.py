"""
Metrics and waveform alignment for the rectifier study (build spec section 6).

All HB-vs-reference comparisons go through here so the numbers in the notebook,
the tests, and the paper are computed one way.
"""
import numpy as np


def fold_to_period(t, v, f0):
    """Fold an absolute-time transient trace onto one period, sorted by phase
    in [0,1).  Returns (phase, v_sorted)."""
    T0 = 1.0 / f0
    ph = (t % T0) / T0
    order = np.argsort(ph)
    return ph[order], v[order]


def resample(ph_src, v_src, ph_dst):
    """Periodic interpolation of (ph_src, v_src) onto ph_dst in [0,1)."""
    return np.interp(ph_dst, ph_src, v_src, period=1.0)


def nrmse(ref, test):
    """RMSE normalised by the reference peak-to-peak (build spec section 6)."""
    pp = ref.max() - ref.min()
    return np.sqrt(np.mean((test - ref) ** 2)) / pp if pp > 0 else np.nan


def align_and_nrmse(hb_result, node, t_td, v_td, f0, M=2000):
    """Resample HB waveform and a transient trace onto a common phase grid and
    return (phase, v_hb, v_td, nrmse)."""
    ph_dst = np.linspace(0, 1, M, endpoint=False)
    th, vh = hb_result.waveform(node, N=max(M, 8 * hb_result.K))
    v_hb = resample(th * f0, vh, ph_dst)              # th*f0 = phase
    ph_r, v_r = fold_to_period(t_td, v_td, f0)
    v_ref = resample(ph_r, v_r, ph_dst)
    return ph_dst, v_hb, v_ref, nrmse(v_ref, v_hb)


def conduction_angle(i_d, eps_frac=1e-3):
    """Conduction angle in degrees from a one-period diode-current trace:
    fraction of the period where |i_d| exceeds eps_frac of its peak."""
    pk = np.max(np.abs(i_d))
    if pk <= 0:
        return 0.0
    return float(np.mean(np.abs(i_d) > eps_frac * pk) * 360.0)


def thd(harmonics, K):
    """Total harmonic distortion from two-sided coefficients (length 2K+1),
    using one-sided magnitudes:  sqrt(sum_{k>=2} |a_k|^2) / |a_1|."""
    dc = K
    a1 = abs(harmonics[dc + 1])
    if a1 == 0:
        return np.nan
    hi = np.sqrt(sum(abs(harmonics[dc + k]) ** 2 for k in range(2, K + 1)))
    return hi / a1


def per_harmonic_error(hb_harm, ref_harm, K):
    """Per-harmonic magnitude error |k| = 0..K between two two-sided coeff
    arrays.  Returns an array of length K+1 (one-sided)."""
    dc = K
    out = np.zeros(K + 1)
    out[0] = abs(hb_harm[dc] - ref_harm[dc])
    for k in range(1, K + 1):
        out[k] = abs(hb_harm[dc + k] - ref_harm[dc + k])
    return out


def ripple(v):
    return float(v.max() - v.min())


def vdc(v):
    return float(np.mean(v))
