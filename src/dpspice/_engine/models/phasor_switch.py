"""
Phasor-domain PWM switch model using ideal transformer with complex turns ratio.

Prof. Rim's insight: a PWM switch in the phasor domain behaves as an ideal
transformer whose turns ratio is complex:

    S = D * exp(j * theta)

where D is the duty ratio (0-1) and theta is the switching phase angle (rad).

This is the foundation for all phasor-equivalent inverter simulation in DPSpice.
The ideal transformer enforces:

    V_primary = S * V_secondary     (voltage constraint)
    I_secondary = S* * I_primary    (current, conjugate for power conservation)

In MNA terms, this adds one auxiliary variable I_t (transformer branch current)
and two stamp rows:

    KCL at n1:  -I_t                 (current leaves primary)
    KCL at n2:  +conj(S) * I_t       (conjugate current enters secondary)
    KVL row:    V(n1) - S * V(n2) = 0

The stamp is NON-SYMMETRIC: the C block = B^H (Hermitian transpose of B),
which is critical for complex turns ratios.

Reference:
    C. T. Rim, "Unified General Phasor Transformations for AC/DC/AC converters,"
    IEEE Trans. Power Electron., 2025.

Author: Doyun Gu (University of Manchester)
"""

import numpy as np
from dataclasses import dataclass
from typing import Optional


@dataclass
class PhasorSwitch:
    """
    Phasor-domain PWM switch (ideal transformer with complex turns ratio).

    Parameters
    ----------
    from_bus : int
        Primary-side node index (n1)
    to_bus : int
        Secondary-side node index (n2)
    duty_ratio : float
        D, magnitude of the turns ratio (0-1)
    phase : float
        theta, switching phase angle in radians
    name : str, optional
        Element name for labelling (default: auto-generated)

    Properties
    ----------
    turns_ratio : complex
        S = D * exp(j * theta) -- the complex turns ratio
    """
    from_bus: int
    to_bus: int
    duty_ratio: float   # D, magnitude (0-1)
    phase: float         # theta, radians
    name: str = ""

    def __post_init__(self):
        if not 0.0 <= self.duty_ratio <= 1.0:
            raise ValueError(
                f"Duty ratio must be in [0, 1], got {self.duty_ratio}"
            )
        if not self.name:
            self.name = f"T_ps_{self.from_bus}_{self.to_bus}"

    @property
    def turns_ratio(self) -> complex:
        """Complex turns ratio S = D * exp(j * theta)."""
        return self.duty_ratio * np.exp(1j * self.phase)


def stamp_ideal_transformer(
    A: np.ndarray,
    n1: int,
    n2: int,
    n_ratio: complex,
    branch_idx: int,
) -> None:
    """
    Stamp an ideal transformer into the MNA A matrix.

    Adds one auxiliary variable I_t at index ``branch_idx``.

    The stamp is:
        B column (KCL):
            A[n1, branch_idx] += -1        (current out of primary)
            A[n2, branch_idx] += conj(n)   (conjugate current into secondary)
        C row (KVL):
            A[branch_idx, n1] += 1         (V_primary ...)
            A[branch_idx, n2] += -n        (... minus n * V_secondary = 0)

    Note: C = B^H (Hermitian), NOT B^T. This is the correct formulation
    for complex turns ratios that preserves power conservation.

    Parameters
    ----------
    A : ndarray
        MNA system matrix (will be modified in place). Must be complex dtype.
    n1 : int
        Primary node matrix index (-1 for ground)
    n2 : int
        Secondary node matrix index (-1 for ground)
    n_ratio : complex
        Complex turns ratio S = D * exp(j*theta)
    branch_idx : int
        Row/column index for the transformer branch current I_t
    """
    n_conj = np.conj(n_ratio)

    # B column: KCL stamps (current distribution)
    if n1 >= 0:
        A[n1, branch_idx] += -1.0       # I_t leaves primary node
    if n2 >= 0:
        A[n2, branch_idx] += n_conj     # conj(S)*I_t enters secondary node

    # C row: KVL voltage constraint V(n1) - S*V(n2) = 0
    if n1 >= 0:
        A[branch_idx, n1] += 1.0        # +V(n1)
    if n2 >= 0:
        A[branch_idx, n2] += -n_ratio   # -S*V(n2)
