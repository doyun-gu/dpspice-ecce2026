"""
Multi-Harmonic Dynamic Phasor (MHDP) for switching circuits.

Generalises the single-frequency dynamic-phasor shift  A -> A - j w E  to a
stack of 2K+1 harmonic blocks coupled by the switching function.  Built on top
of the DPSpice automatic MNA build (build_mna): the engine supplies E, A and
b(t); this module adds the switch stamp, the Fourier/Toeplitz coupling, the
harmonic-balance steady-state solve, the transient-envelope integrator, and a
fixed-step trapezoidal time-domain reference with a toggled switch.

State equation (engine convention):   E dx/dt = A x + b(t)
Switch branch:   conductance g(t) = g_off + (g_on - g_off) s(t)   stamped as
A += g(t) P, where P is the branch incidence (P[i,i]=-1, P[i,j]=+1, ...).

Author: Doyun Gu (University of Manchester) -- ECCE 2026 switching extension.
"""
import os, sys
import numpy as np

# Vendored alongside this module inside dpspice._engine; the package's
# __init__ puts this directory on sys.path so the flat imports resolve.
from netlist_parser import parse_ltspice_netlist   # noqa: E402
from mna_circuit import build_mna                   # noqa: E402


# ----------------------------------------------------------------------------
# MNA + switch
# ----------------------------------------------------------------------------
def build_system(netlist_str):
    """Parse a netlist and build the complex MNA system (switch excluded)."""
    nl = parse_ltspice_netlist(netlist_str)
    mna = build_mna(nl)
    return mna, mna.E.astype(complex), mna.A.astype(complex), mna.n_total


def switch_stamp(mna, node_a, node_b):
    """Branch-incidence matrix P for a conductance between node_a and node_b.
    Engine sign convention: stamping conductance g  =>  A += g * P."""
    n = mna.n_total
    ia = mna.node_map.idx(node_a)
    ib = mna.node_map.idx(node_b)
    P = np.zeros((n, n), complex)
    for x in (ia, ib):
        if x >= 0:
            P[x, x] -= 1.0
    if ia >= 0 and ib >= 0:
        P[ia, ib] += 1.0
        P[ib, ia] += 1.0
    return P


# ----------------------------------------------------------------------------
# Fourier coefficients (from the actual signals)
# ----------------------------------------------------------------------------
def square_gate(t, f0, duty=0.5):
    """Unit square-wave gate, synchronised to the fundamental at t=0."""
    return (np.mod(t * f0, 1.0) < duty).astype(float)

def fourier_coeffs(samples, axis=0):
    """Two-sided complex Fourier coefficients c_k of one period sampled
    uniformly: x(t)=sum_k c_k e^{j k w t}.  c_k = FFT(x)[k]/N."""
    N = samples.shape[axis]
    return np.fft.fft(samples, axis=axis) / N

def gate_coeffs(f0, duty, NF=4096):
    tg = np.arange(NF) / NF * (1.0 / f0)
    return fourier_coeffs(square_gate(tg, f0, duty)), NF

def source_coeffs(mna, f0, NF=4096):
    tg = np.arange(NF) / NF * (1.0 / f0)
    b = np.array([mna.b_func(t).copy() for t in tg])
    return fourier_coeffs(b, axis=0), NF


# ----------------------------------------------------------------------------
# MHDP assembly (the shift, generalised)
# ----------------------------------------------------------------------------
def assemble(E, A, P, S, B, NF, w, K, g_on, g_off, coupled=True):
    """Stack 2K+1 harmonic blocks.
        diag (k,k):  A - j k w E + (g_off + dG S_0) P
        off  (k,k'): dG S_{k-k'} P          (the switching convolution)
    coupled=False zeroes the off-diagonal blocks (= independent-harmonic
    solve, what a per-harmonic DP method does -- used to show coupling matters).
    Returns E_big, A_big, B_big, ks."""
    dG = g_on - g_off
    n = E.shape[0]
    ks = np.arange(-K, K + 1)
    nB = len(ks)
    Eb = np.zeros((nB * n, nB * n), complex)
    Ab = np.zeros((nB * n, nB * n), complex)
    Bb = np.zeros(nB * n, complex)
    sl = lambda a: slice(a * n, (a + 1) * n)
    for a, k in enumerate(ks):
        Eb[sl(a), sl(a)] = E
        Ab[sl(a), sl(a)] = A - 1j * k * w * E + (g_off + dG * S[0]) * P
        Bb[sl(a)] = B[k % NF]
        if coupled:
            for b_, kp in enumerate(ks):
                m = k - kp
                if m != 0:
                    Ab[sl(a), sl(b_)] += dG * S[m % NF] * P
    return Eb, Ab, Bb, ks


def steady_state(E, A, P, S, B, NF, w, K, g_on, g_off, coupled=True):
    """Harmonic balance: one linear solve A_big X = -B_big for the periodic
    operating waveform (no cycle-by-cycle settling)."""
    Eb, Ab, Bb, ks = assemble(E, A, P, S, B, NF, w, K, g_on, g_off, coupled)
    X = np.linalg.solve(Ab, -Bb).reshape(len(ks), -1)
    return X, ks


def reconstruct(X, ks, t, w):
    """x(t) = sum_k X_k e^{j k w t}; real by conjugate symmetry."""
    t = np.asarray(t)
    out = np.zeros((len(t), X.shape[1]), complex)
    for Xrow, k in zip(X, ks):
        out += np.outer(np.exp(1j * k * w * t), Xrow)
    return out.real


def transient_envelope(E, A, P, S, B, NF, w, K, g_on, g_off, t_end, n_steps):
    """Integrate the stacked envelope  E_big dX/dt = A_big X + B_big
    (fixed-step trapezoidal) from rest -- start-up dynamics of the phasors."""
    Eb, Ab, Bb, ks = assemble(E, A, P, S, B, NF, w, K, g_on, g_off, True)
    dt = t_end / n_steps
    nB = len(ks) * E.shape[0]
    X = np.zeros(nB, complex)
    M = Eb - 0.5 * dt * Ab
    Minv_rhsA = np.linalg.inv(M) @ (Eb + 0.5 * dt * Ab)
    Minv_b = np.linalg.solve(M, dt * Bb)
    traj = np.zeros((n_steps + 1, len(ks), E.shape[0]), complex)
    traj[0] = X.reshape(len(ks), -1)
    for i in range(n_steps):
        X = Minv_rhsA @ X + Minv_b
        traj[i + 1] = X.reshape(len(ks), -1)
    t = np.linspace(0, t_end, n_steps + 1)
    return t, traj, ks


# ----------------------------------------------------------------------------
# Time-domain reference (ground truth): trapezoidal, switch toggled each step
# ----------------------------------------------------------------------------
def time_domain(E, A, P, mna, f0, g_on, g_off, duty=0.5,
                n_cycles=400, steps_per_cycle=800, keep_cycles=2, x0=None):
    """Fixed-step trapezoidal solve of E dx/dt = A(t) x + b(t) with the switch
    conductance toggled by the gate.  Returns the last keep_cycles (for steady
    state) OR the full trajectory if full=True."""
    n = E.shape[0]
    dG = g_on - g_off
    T = 1.0 / f0
    dt = T / steps_per_cycle
    N = n_cycles * steps_per_cycle
    x = np.zeros(n, complex) if x0 is None else x0.astype(complex)
    keep = keep_cycles * steps_per_cycle
    buf = np.zeros((keep, n), complex)
    tb = np.zeros(keep)
    g = lambda t: g_off + dG * (1.0 if (np.mod(t * f0, 1.0) < duty) else 0.0)
    t = 0.0
    An = A + g(t) * P
    bn = mna.b_func(t).copy()
    for i in range(N):
        t1 = t + dt
        A1 = A + g(t1) * P
        b1 = mna.b_func(t1).copy()
        rhs = (E + 0.5 * dt * An) @ x + 0.5 * dt * (bn + b1)
        x = np.linalg.solve(E - 0.5 * dt * A1, rhs)
        An, bn, t = A1, b1, t1
        if i >= N - keep:
            j = i - (N - keep)
            buf[j] = x
            tb[j] = t1
    return tb, buf.real


def time_domain_full(E, A, P, mna, f0, g_on, g_off, duty=0.5,
                     n_cycles=80, steps_per_cycle=400):
    """Full start-up trajectory (for the transient-envelope overlay)."""
    n = E.shape[0]; dG = g_on - g_off; T = 1.0 / f0
    dt = T / steps_per_cycle; N = n_cycles * steps_per_cycle
    x = np.zeros(n, complex)
    traj = np.zeros((N + 1, n), complex); tt = np.zeros(N + 1)
    g = lambda t: g_off + dG * (1.0 if (np.mod(t * f0, 1.0) < duty) else 0.0)
    t = 0.0; An = A + g(t) * P; bn = mna.b_func(t).copy()
    for i in range(N):
        t1 = t + dt; A1 = A + g(t1) * P; b1 = mna.b_func(t1).copy()
        x = np.linalg.solve(E - 0.5 * dt * A1, (E + 0.5 * dt * An) @ x + 0.5 * dt * (bn + b1))
        An, bn, t = A1, b1, t1
        traj[i + 1] = x; tt[i + 1] = t1
    return tt, traj.real


def nrmse(ref, test):
    """RMSE normalised by the RMS of the reference."""
    rms = np.sqrt(np.mean(ref ** 2))
    return np.sqrt(np.mean((ref - test) ** 2)) / rms if rms > 0 else np.nan
