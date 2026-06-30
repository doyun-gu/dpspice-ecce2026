"""
Alternating Frequency-Time (AFT) primitives for the rectifier harmonic-balance
solver.  These are the device-agnostic building blocks of the HB Newton loop
(see rectifier/README.md, build spec sections 1.3-1.4):

    iFFT:  harmonic coefficients  ->  time samples over one period
    FFT :  time samples           ->  harmonic coefficients (Hermitian-clean)
    Toeplitz(G): spectral Jacobian block from the harmonics of g(t)=I'(v(t))

Harmonic convention (matches the engine + mhdp.py):
    x(t) = sum_{k=-K..K} X_k e^{j k w0 t},   X_{-k} = conj(X_k)  for real x.

A single signal's coefficients are stored as a 1-D complex array of length
2*K+1 ordered k = -K, ..., 0, ..., +K  (DC at index K).  Multi-node stacks use
the layout  [node0 (H coeffs), node1 (H coeffs), ...] only in the solver; the
helpers here act per-signal and are vectorised over a trailing node axis.

Author: Doyun Gu (University of Manchester) -- ECCE 2026 nonlinear extension.
"""
import numpy as np


def n_harmonics(K):
    """Number of stored two-sided coefficients for truncation order K."""
    return 2 * K + 1


def oversample_N(K, factor=8):
    """Time-grid length for AFT: next power of two >= factor*(2K).  The
    exponential diode nonlinearity is harmonic-rich, so we oversample well
    beyond the Nyquist floor (2*(2K)+1) to suppress aliasing (spec 1.3)."""
    floor = factor * (2 * K)
    N = 1
    while N < max(floor, 4):
        N <<= 1
    return N


def hermitian_clean(Xk, K):
    """Enforce X_{-k} = conj(X_k) and a real DC term on a (2K+1,) coeff array
    (or (..., 2K+1) with harmonics on the last axis).  Kills the small
    imaginary leakage that FFT round-off puts on a nominally real signal."""
    Xk = np.asarray(Xk, dtype=complex)
    out = Xk.copy()
    dc = K
    out[..., dc] = out[..., dc].real
    for k in range(1, K + 1):
        avg = 0.5 * (out[..., dc + k] + np.conj(out[..., dc - k]))
        out[..., dc + k] = avg
        out[..., dc - k] = np.conj(avg)
    return out


def to_time(Xk, K, N):
    """Inverse transform: coefficients -> N uniform time samples over one
    period.  Xk has harmonics on the last axis (shape (..., 2K+1)); returns the
    real time series with samples on the last axis (shape (..., N))."""
    Xk = np.asarray(Xk, dtype=complex)
    lead = Xk.shape[:-1]
    spec = np.zeros(lead + (N,), dtype=complex)
    dc = K
    spec[..., 0] = Xk[..., dc]
    for k in range(1, K + 1):
        spec[..., k] = Xk[..., dc + k]
        spec[..., N - k] = Xk[..., dc - k]
    # x[m] = sum_k X_k e^{j 2pi k m / N} = N * ifft(spec)[m]
    x = np.fft.ifft(spec, axis=-1) * N
    return x.real


def to_freq(x, K, N, Kc=None, clean=True):
    """Forward transform: N real time samples -> two-sided coefficients up to
    order Kc (default K).  x has samples on the last axis (shape (..., N));
    returns coeffs on the last axis (shape (..., 2*Kc+1)), Hermitian-cleaned.

    Kc > K is used to gather the +-2K harmonics of g(t) needed for the
    Toeplitz Jacobian block (spec 1.4)."""
    if Kc is None:
        Kc = K
    x = np.asarray(x)
    F = np.fft.fft(x, axis=-1) / N
    lead = x.shape[:-1]
    Xk = np.zeros(lead + (2 * Kc + 1,), dtype=complex)
    dc = Kc
    Xk[..., dc] = F[..., 0]
    for k in range(1, Kc + 1):
        Xk[..., dc + k] = F[..., k]
        Xk[..., dc - k] = F[..., N - k]
    return hermitian_clean(Xk, Kc) if clean else Xk


def coeff(Gk, Kc, k):
    """Fetch the k-th coefficient from a two-sided array indexed -Kc..Kc.
    Returns 0 for |k| > Kc (out-of-band harmonics are truncated)."""
    if abs(k) > Kc:
        return 0.0
    return Gk[..., Kc + k]


def toeplitz_block(Gk, K, Kc=None):
    """Spectral Jacobian block of a per-node time-varying conductance g(t):
        J[a,b] = G_{(k_a - k_b)},   k = -K..K
    where Gk holds the two-sided harmonics of g(t) up to order Kc (>= 2K).
    Returns an (H, H) complex matrix, H = 2K+1 (spec 1.4)."""
    if Kc is None:
        Kc = (Gk.shape[-1] - 1) // 2
    H = n_harmonics(K)
    ks = np.arange(-K, K + 1)
    T = np.zeros((H, H), dtype=complex)
    for a, ka in enumerate(ks):
        for b, kb in enumerate(ks):
            T[a, b] = coeff(Gk, Kc, ka - kb)
    return T
