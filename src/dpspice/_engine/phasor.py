"""
Phasor transformation implementations.

This module provides both instantaneous dynamic phasor (IDP) and
generalized averaging phasor methods for circuit analysis.

Reference:
    C. T. Rim, C. Mi, et al., "General instantaneous dynamic phasor for time-varying signals and systems," IEEE Trans. Power Electron., vol. 40, no. 11, 2025
    Sanders et al., "Generalized averaging method," IEEE TPEL 1991
"""

import numpy as np
from typing import Callable, Union, Tuple
from dataclasses import dataclass
from enum import Enum
from abc import ABC


class PhasorMethod(Enum):
    """Available phasor transformation methods."""
    INSTANTANEOUS = "instantaneous"
    AVERAGED = "averaged"
    HYBRID = "hybrid"


@dataclass
class PhasorConfig:
    """Configuration for phasor transformations."""
    omega: float  # Base angular frequency (rad/s)
    m: int = 1    # Number of phases (1=single, 3=three-phase)

    # For generalized averaging
    num_harmonics: int = 1  # Number of harmonics to track

    # For FM/time-varying frequency
    omega_0: float = None   # Base frequency for FM
    omega_1: float = None   # Modulation frequency
    alpha: float = 0.0      # FM modulation index

    def __post_init__(self):
        if self.omega_0 is None:
            self.omega_0 = self.omega


class InstantaneousPhasor:
    """
    Instantaneous Dynamic Phasor transformation.

    Implements Eq. (1) from Rim et al. (2025):
        x(t) = Re{(1/sqrt(m)) * x_tilde(t) * e^(j*theta(t))}

    Key property: Valid instantaneously for ANY time, including
    discontinuous signals and time-varying frequencies.

    Parameters
    ----------
    config : PhasorConfig
        Configuration including frequency and phase parameters
    theta_func : callable, optional
        Custom phase function theta(t). If None, uses theta(t) = omega*t

    Examples
    --------
    >>> config = PhasorConfig(omega=580e3)
    >>> phasor = InstantaneousPhasor(config)
    >>> x_phasor = phasor.to_phasor(x_real, t)
    >>> x_recovered = phasor.to_real(x_phasor, t)
    """

    def __init__(self, config: PhasorConfig,
                 theta_func: Callable[[float], float] = None):
        self.config = config
        self._sqrt_m = np.sqrt(config.m)

        if theta_func is not None:
            self.theta = theta_func
        elif config.alpha != 0:
            # FM case: theta(t) = omega_0*t + alpha*sin(omega_1*t)  [Eq. 2]
            self.theta = lambda t: (config.omega_0 * t +
                                   config.alpha * np.sin(config.omega_1 * t))
        else:
            # Standard case: theta(t) = omega*t
            self.theta = lambda t: config.omega * t

    def theta_dot(self, t: Union[float, np.ndarray]) -> Union[float, np.ndarray]:
        """
        Time derivative of phase angle d_theta/dt.

        For theta(t) = omega_0*t + alpha*sin(omega_1*t):
            d_theta/dt = omega_0 + alpha*omega_1*cos(omega_1*t)  [used in Eq. 36]
        """
        if self.config.alpha != 0:
            return (self.config.omega_0 +
                   self.config.alpha * self.config.omega_1 *
                   np.cos(self.config.omega_1 * t))
        return self.config.omega

    def to_phasor(self, x_real: np.ndarray, t: np.ndarray) -> np.ndarray:
        """
        Transform real signal to phasor space.

        Given x(t), compute x_tilde(t) = sqrt(m) * x(t) * e^(-j*theta(t))  [Eq. 3]

        Parameters
        ----------
        x_real : ndarray
            Real-space signal x(t)
        t : ndarray
            Time vector

        Returns
        -------
        ndarray
            Complex phasor signal x_tilde(t)
        """
        theta = self.theta(t)
        return self._sqrt_m * x_real * np.exp(-1j * theta)

    def to_real(self, x_phasor: np.ndarray, t: np.ndarray) -> np.ndarray:
        """
        Transform phasor signal back to real space.

        x(t) = Re{(1/sqrt(m)) * x_tilde(t) * e^(j*theta(t))}  [Eq. 1]

        Parameters
        ----------
        x_phasor : ndarray
            Complex phasor signal x_tilde(t)
        t : ndarray
            Time vector

        Returns
        -------
        ndarray
            Real-space signal x(t)
        """
        theta = self.theta(t)
        return np.real((1.0 / self._sqrt_m) * x_phasor * np.exp(1j * theta))

    def envelope(self, x_phasor: np.ndarray) -> np.ndarray:
        """Extract envelope (magnitude) of phasor signal."""
        return np.abs(x_phasor)

    def phase(self, x_phasor: np.ndarray) -> np.ndarray:
        """Extract phase angle of phasor signal."""
        return np.angle(x_phasor)

    def reactance_L(self, L: float, t: Union[float, np.ndarray]) -> Union[float, np.ndarray]:
        """
        Time-varying inductive reactance.

        X_L(t) = d_theta/dt * L  [Eq. 8, 36a]
        """
        return self.theta_dot(t) * L

    def reactance_C(self, C: float, t: Union[float, np.ndarray]) -> Union[float, np.ndarray]:
        """
        Time-varying capacitive reactance.

        X_C(t) = -1/(d_theta/dt * C)  [Eq. 9c, 36b]
        """
        theta_d = self.theta_dot(t)
        return -1.0 / (theta_d * C)


class GeneralizedAveraging:
    """
    Generalized Averaging method for dynamic phasors.

    Implements Eq. (12)-(14) from Sanders et al. (1991):
        <x>_k(t) = (1/T) integral_0^T x(t-T+s) * e^(-jk*omega*s(t-T+s)) ds

    Note: This method requires integration over one period T,
    so it introduces a delay and is not instantaneously valid.

    Parameters
    ----------
    config : PhasorConfig
        Configuration including frequency and harmonics
    """

    def __init__(self, config: PhasorConfig):
        self.config = config
        self.T = 2 * np.pi / config.omega  # Period

    def fourier_coefficient(self, x: np.ndarray, t: np.ndarray,
                           k: int = 1) -> np.ndarray:
        """
        Compute k-th Fourier coefficient <x>_k(t).

        Uses sliding window integration [Eq. 13].

        Parameters
        ----------
        x : ndarray
            Real signal
        t : ndarray
            Time vector
        k : int
            Harmonic number (default 1 = fundamental)

        Returns
        -------
        ndarray
            Complex Fourier coefficient over time
        """
        dt = t[1] - t[0]
        samples_per_period = int(np.round(self.T / dt))

        if samples_per_period < 2:
            raise ValueError("Time resolution too coarse for averaging period")

        result = np.zeros(len(t), dtype=complex)

        for i in range(samples_per_period, len(t)):
            # Window from t-T to t
            window_idx = slice(i - samples_per_period, i)
            window_t = t[window_idx]
            window_x = x[window_idx]

            # Integration kernel
            kernel = np.exp(-1j * k * self.config.omega * window_t)

            # Trapezoidal integration
            result[i] = np.trapezoid(window_x * kernel, window_t) / self.T

        # Handle initial period (insufficient data)
        result[:samples_per_period] = result[samples_per_period]

        return result

    def to_phasor(self, x_real: np.ndarray, t: np.ndarray,
                  k: int = 1) -> np.ndarray:
        """
        Transform real signal to k-th harmonic phasor.

        Parameters
        ----------
        x_real : ndarray
            Real-space signal
        t : ndarray
            Time vector
        k : int
            Harmonic number

        Returns
        -------
        ndarray
            Complex phasor (Fourier coefficient)
        """
        return self.fourier_coefficient(x_real, t, k)

    def to_real(self, coefficients: dict, t: np.ndarray) -> np.ndarray:
        """
        Reconstruct real signal from Fourier coefficients.

        x(t) = sum_k <x>_k(t) * e^(jk*omega*t)  [Eq. 12]

        Parameters
        ----------
        coefficients : dict
            Dictionary {k: <x>_k(t)} of harmonic coefficients
        t : ndarray
            Time vector

        Returns
        -------
        ndarray
            Reconstructed real signal
        """
        result = np.zeros(len(t))

        for k, coeff in coefficients.items():
            result += np.real(coeff * np.exp(1j * k * self.config.omega * t))

        return result

    def derivative_relation(self, x_k: np.ndarray, dx_dt_k: np.ndarray,
                           k: int = 1) -> np.ndarray:
        """
        Apply derivative relation [Eq. 14]:
            d/dt<x>_k = <dx/dt>_k - jk*omega*<x>_k

        Rearranged:
            <dx/dt>_k = d/dt<x>_k + jk*omega*<x>_k
        """
        return dx_dt_k + 1j * k * self.config.omega * x_k


class HybridPhasor:
    """
    Hybrid phasor method that automatically selects between
    instantaneous and averaged approaches.

    Selection criteria:
    - Use instantaneous for: fast transients, switching events,
      time-varying frequency
    - Use averaged for: steady-state, slowly-varying envelopes,
      harmonic analysis

    Parameters
    ----------
    config : PhasorConfig
        Configuration parameters
    bandwidth_threshold : float
        Ratio of signal bandwidth to carrier frequency that
        triggers switch to instantaneous method
    """

    def __init__(self, config: PhasorConfig, bandwidth_threshold: float = 0.1):
        self.config = config
        self.bandwidth_threshold = bandwidth_threshold

        self.instantaneous = InstantaneousPhasor(config)
        self.averaged = GeneralizedAveraging(config)

        self._method_history = []

    def select_method(self, x: np.ndarray, t: np.ndarray) -> PhasorMethod:
        """
        Automatically select the best method for the given signal.

        Criteria:
        1. If FM modulation present -> Instantaneous
        2. If signal has fast transients -> Instantaneous
        3. If steady-state with slow envelope -> Averaged
        """
        # Check for FM modulation
        if self.config.alpha != 0:
            return PhasorMethod.INSTANTANEOUS

        # Estimate signal bandwidth from envelope variation
        envelope_approx = np.abs(x)
        if len(envelope_approx) > 10:
            envelope_variation = np.std(np.diff(envelope_approx)) / (np.mean(envelope_approx) + 1e-10)

            # Normalize by expected steady-state variation
            relative_bandwidth = envelope_variation * self.config.omega

            if relative_bandwidth > self.bandwidth_threshold:
                return PhasorMethod.INSTANTANEOUS

        return PhasorMethod.AVERAGED

    def to_phasor(self, x_real: np.ndarray, t: np.ndarray,
                  method: PhasorMethod = None) -> Tuple[np.ndarray, PhasorMethod]:
        """
        Transform to phasor using automatically selected or specified method.

        Parameters
        ----------
        x_real : ndarray
            Real signal
        t : ndarray
            Time vector
        method : PhasorMethod, optional
            Force specific method. If None, auto-select.

        Returns
        -------
        x_phasor : ndarray
            Complex phasor signal
        method_used : PhasorMethod
            Method that was actually used
        """
        if method is None:
            method = self.select_method(x_real, t)

        self._method_history.append(method)

        if method == PhasorMethod.INSTANTANEOUS:
            return self.instantaneous.to_phasor(x_real, t), method
        else:
            return self.averaged.to_phasor(x_real, t), method

    def to_real(self, x_phasor: np.ndarray, t: np.ndarray,
                method: PhasorMethod = PhasorMethod.INSTANTANEOUS) -> np.ndarray:
        """Transform phasor back to real space."""
        if method == PhasorMethod.INSTANTANEOUS:
            return self.instantaneous.to_real(x_phasor, t)
        else:
            return self.averaged.to_real({1: x_phasor}, t)


# Utility functions

def create_phasor_transform(omega: float, method: str = "instantaneous",
                           **kwargs) -> Union[InstantaneousPhasor, GeneralizedAveraging, HybridPhasor]:
    """
    Factory function to create phasor transform.

    Parameters
    ----------
    omega : float
        Angular frequency in rad/s
    method : str
        One of "instantaneous", "averaged", "hybrid"
    **kwargs :
        Additional parameters for PhasorConfig

    Returns
    -------
    Phasor transform object
    """
    config = PhasorConfig(omega=omega, **kwargs)

    if method == "instantaneous":
        return InstantaneousPhasor(config)
    elif method == "averaged":
        return GeneralizedAveraging(config)
    elif method == "hybrid":
        return HybridPhasor(config)
    else:
        raise ValueError(f"Unknown method: {method}")


def compare_methods(x_real: np.ndarray, t: np.ndarray, omega: float) -> dict:
    """
    Compare instantaneous and averaged phasor methods on same signal.

    Returns dictionary with phasors and reconstruction errors for both methods.
    """
    config = PhasorConfig(omega=omega)
    inst = InstantaneousPhasor(config)
    avg = GeneralizedAveraging(config)

    # Transform and reconstruct
    x_inst = inst.to_phasor(x_real, t)
    x_avg = avg.to_phasor(x_real, t)

    x_rec_inst = inst.to_real(x_inst, t)
    x_rec_avg = avg.to_real({1: x_avg}, t)

    # Compute errors
    err_inst = np.sqrt(np.mean((x_real - x_rec_inst)**2))
    err_avg = np.sqrt(np.mean((x_real - x_rec_avg)**2))

    return {
        'instantaneous': {
            'phasor': x_inst,
            'reconstruction': x_rec_inst,
            'rmse': err_inst
        },
        'averaged': {
            'phasor': x_avg,
            'reconstruction': x_rec_avg,
            'rmse': err_avg
        }
    }
