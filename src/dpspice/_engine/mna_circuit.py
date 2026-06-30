"""
Generic MNA-based circuit for dynamic phasor simulation.

Builds Modified Nodal Analysis (MNA) state equations automatically from a
parsed LTspice netlist, then solves them in both time domain and phasor domain
using the existing dynamic phasor framework.

The MNA formulation:
    [G  B] [v]   [C_v  0 ] d [v]   [i_s(t)]
    [B' 0] [j] + [0    L ] dt[j] = [v_s(t)]

where:
    v = node voltages (unknowns)
    j = voltage-source / inductor branch currents (unknowns)
    G = conductance matrix (from R, C, I sources)
    B = incidence of voltage sources & inductors
    C_v = capacitance contribution matrix
    L = inductance matrix
    i_s(t) = current source excitations
    v_s(t) = voltage source excitations

State vector x = [v_nodes; i_branches], dynamics: E*dx/dt = A*x + b(t)

Reference:
    Chung-Wen Ho, A. Ruehli, P. Brennan, "The Modified Nodal Approach to
    Network Analysis," IEEE TCAS, 1975.

Author: Doyun Gu (University of Manchester)
"""

import numpy as np
from scipy.integrate import solve_ivp
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Callable
import warnings

from netlist_parser import (
    ParsedNetlist, SourceType,
    parse_ltspice_netlist,
)
from phasor import PhasorConfig, InstantaneousPhasor
from models.phasor_switch import PhasorSwitch, stamp_ideal_transformer

# Optional C++ acceleration backend
try:
    import dpspice_cpp
    HAS_CPP = True
except ImportError:
    HAS_CPP = False


# ----------------------------------------------------------------------
# Node indexing helper
# ----------------------------------------------------------------------

class NodeMap:
    """
    Maps node names to integer indices for matrix construction.
    Ground node (0, GND, gnd, ground) always maps to index -1 (excluded).
    """
    GROUND_NAMES = {'0', 'gnd', 'GND', 'ground', 'GROUND'}

    def __init__(self, nodes: set):
        non_ground = sorted(nodes - self.GROUND_NAMES)
        self._name_to_idx = {name: i for i, name in enumerate(non_ground)}
        self._idx_to_name = {i: name for name, i in self._name_to_idx.items()}
        self.n = len(non_ground)

    def idx(self, name: str) -> int:
        """Return matrix index for node. Returns -1 for ground."""
        if name in self.GROUND_NAMES:
            return -1
        return self._name_to_idx[name]

    def name(self, idx: int) -> str:
        return self._idx_to_name[idx]

    def is_ground(self, name: str) -> bool:
        return name in self.GROUND_NAMES

    def names(self) -> List[str]:
        return [self._idx_to_name[i] for i in range(self.n)]


# ----------------------------------------------------------------------
# MNA builder
# ----------------------------------------------------------------------

@dataclass
class MNASystem:
    """
    The MNA system matrices: E * dx/dt = A * x + b(t)

    State vector x = [v1, v2, ..., vN, iV1, iV2, ..., iL1, iL2, ..., iT1, iT2, ...]
    where the first N entries are node voltages and the remaining are
    branch currents through voltage sources, inductors, and ideal transformers.

    Attributes
    ----------
    A : ndarray (n_total x n_total)
        System matrix (contains -G and branch contributions).
        Complex dtype when ideal transformers with complex turns ratios are present.
    E : ndarray (n_total x n_total)
        Mass matrix (contains C and L)
    b_func : callable
        Returns b(t) excitation vector at time t
    n_nodes : int
        Number of non-ground nodes
    n_vsrc : int
        Number of voltage source branches
    n_ind : int
        Number of inductor branches
    n_xfmr : int
        Number of ideal transformer branches
    n_total : int
        Total system size = n_nodes + n_vsrc + n_ind + n_xfmr
    node_map : NodeMap
        Mapping of node names to indices
    vsrc_names : list of str
        Voltage source branch names (in order)
    ind_names : list of str
        Inductor branch names (in order)
    xfmr_names : list of str
        Ideal transformer branch names (in order)
    state_labels : list of str
        Human-readable label for each state variable
    """
    A: np.ndarray
    E: np.ndarray
    b_func: Callable[[float], np.ndarray]
    n_nodes: int
    n_vsrc: int
    n_ind: int
    n_total: int
    node_map: NodeMap
    n_xfmr: int = 0
    vsrc_names: List[str] = field(default_factory=list)
    ind_names: List[str] = field(default_factory=list)
    xfmr_names: List[str] = field(default_factory=list)
    state_labels: List[str] = field(default_factory=list)


def build_mna(
    netlist: ParsedNetlist,
    phasor_switches: Optional[List[PhasorSwitch]] = None,
) -> MNASystem:
    """
    Build the MNA system matrices from a parsed netlist.

    Strategy:
    - Resistors -> stamp into G (conductance matrix)
    - Capacitors -> stamp into C_v (capacitance part of E)
    - Inductors -> add branch current variable, stamp into L part of E
    - Voltage sources -> add branch current variable, KVL constraint
    - Current sources -> stamp into b(t) excitation vector
    - Ideal transformers (T elements / PhasorSwitch) -> branch current + KVL
      with complex turns ratio (NON-SYMMETRIC stamp: C = B^H)

    Parameters
    ----------
    netlist : ParsedNetlist
        Parsed netlist from the LTspice parser
    phasor_switches : list of PhasorSwitch, optional
        Additional ideal transformers with complex turns ratios to stamp.
        These are added alongside any 'T' elements found in the netlist.

    Returns
    -------
    MNASystem
        Complete MNA system ready for ODE solving
    """
    if phasor_switches is None:
        phasor_switches = []

    node_map = NodeMap(netlist.nodes)
    n_nodes = node_map.n

    # Count additional branch variables
    vsrc_list = netlist.voltage_sources()
    ind_list = netlist.inductors()
    xfmr_list = netlist.transformers()
    n_vsrc = len(vsrc_list)
    n_ind = len(ind_list)
    n_xfmr_netlist = len(xfmr_list)
    n_xfmr_switch = len(phasor_switches)
    n_xfmr = n_xfmr_netlist + n_xfmr_switch
    n_total = n_nodes + n_vsrc + n_ind + n_xfmr

    # Build indices for branch variables
    vsrc_names = [e.name for e in vsrc_list]
    ind_names = [e.name for e in ind_list]
    xfmr_names = [e.name for e in xfmr_list] + [ps.name for ps in phasor_switches]

    # Branch current index offsets
    vsrc_offset = n_nodes
    ind_offset = n_nodes + n_vsrc
    xfmr_offset = n_nodes + n_vsrc + n_ind

    # Determine if complex dtype is needed (any complex turns ratio)
    _has_complex = any(
        ps.phase != 0.0 for ps in phasor_switches
    )
    # Also check netlist 'T' elements -- their value encodes real turns ratio,
    # and params may contain 'phase' for complex
    for elem in xfmr_list:
        if elem.params.get('phase', 0.0) != 0.0:
            _has_complex = True
    _dtype = complex if _has_complex else float

    # Initialise matrices
    # A * x is the "static" part:  -(G*v + B*j) for node equations
    #                                B'*v        for branch equations
    # E * dx/dt is the "dynamic" part: C*dv/dt, L*di/dt
    A = np.zeros((n_total, n_total), dtype=_dtype)
    E = np.zeros((n_total, n_total), dtype=_dtype)

    # --- Stamp resistors into G (rows/cols 0..n_nodes-1) ---------
    for elem in netlist.resistors():
        if elem.value == 0:
            warnings.warn(f"Zero resistance in {elem.name}, skipping")
            continue
        g = 1.0 / elem.value  # conductance
        np_idx = node_map.idx(elem.nodes[0])
        nm_idx = node_map.idx(elem.nodes[1])

        # Stamp conductance: G[i,i] += g, G[j,j] += g, G[i,j] -= g, G[j,i] -= g
        if np_idx >= 0:
            A[np_idx, np_idx] -= g
        if nm_idx >= 0:
            A[nm_idx, nm_idx] -= g
        if np_idx >= 0 and nm_idx >= 0:
            A[np_idx, nm_idx] += g
            A[nm_idx, np_idx] += g

    # --- Stamp capacitors into C part of E -----------------------
    for elem in netlist.capacitors():
        c = elem.value
        np_idx = node_map.idx(elem.nodes[0])
        nm_idx = node_map.idx(elem.nodes[1])

        if np_idx >= 0:
            E[np_idx, np_idx] += c
        if nm_idx >= 0:
            E[nm_idx, nm_idx] += c
        if np_idx >= 0 and nm_idx >= 0:
            E[np_idx, nm_idx] -= c
            E[nm_idx, np_idx] -= c

    # --- Stamp voltage sources ------------------------------------
    for k, elem in enumerate(vsrc_list):
        branch_idx = vsrc_offset + k
        np_idx = node_map.idx(elem.nodes[0])
        nm_idx = node_map.idx(elem.nodes[1])

        # KCL: branch current enters n+ and leaves n-
        if np_idx >= 0:
            A[np_idx, branch_idx] += 1.0
            A[branch_idx, np_idx] += 1.0  # KVL: V(n+) - V(n-) = vs(t)
        if nm_idx >= 0:
            A[nm_idx, branch_idx] -= 1.0
            A[branch_idx, nm_idx] -= 1.0

    # --- Stamp inductors (as branch variables like V sources) -----
    for k, elem in enumerate(ind_list):
        branch_idx = ind_offset + k
        np_idx = node_map.idx(elem.nodes[0])
        nm_idx = node_map.idx(elem.nodes[1])

        # KCL contribution: inductor branch current flows n+ -> n-
        # (leaves n+, enters n-) - opposite sign to voltage sources
        if np_idx >= 0:
            A[np_idx, branch_idx] -= 1.0
        if nm_idx >= 0:
            A[nm_idx, branch_idx] += 1.0

        # KVL: V(n+) - V(n-) = L * di/dt
        if np_idx >= 0:
            A[branch_idx, np_idx] += 1.0
        if nm_idx >= 0:
            A[branch_idx, nm_idx] -= 1.0

        # Inductance in mass matrix
        E[branch_idx, branch_idx] = elem.value

    # --- Stamp coupled inductors (K-elements) --------------------
    # K1 L1 L2 k_coupling  =>  M = k * sqrt(L1 * L2)
    # Adds off-diagonal mutual inductance terms to E matrix
    for elem in netlist.coupled_inductors():
        l1_name = elem.nodes[0]  # these are inductor names, not nodes
        l2_name = elem.nodes[1]
        k_coupling = elem.value

        # Find inductor branch indices
        l1_idx = None
        l2_idx = None
        l1_val = 0.0
        l2_val = 0.0
        for ki, ind_elem in enumerate(ind_list):
            if ind_elem.name.lower() == l1_name.lower():
                l1_idx = ind_offset + ki
                l1_val = ind_elem.value
            if ind_elem.name.lower() == l2_name.lower():
                l2_idx = ind_offset + ki
                l2_val = ind_elem.value

        if l1_idx is not None and l2_idx is not None:
            M_mutual = k_coupling * np.sqrt(l1_val * l2_val)
            E[l1_idx, l2_idx] += M_mutual
            E[l2_idx, l1_idx] += M_mutual

    # --- Stamp ideal transformers (T-elements + PhasorSwitch) ------
    # Ideal transformer: V(n1) - n*V(n2) = 0, with non-symmetric stamp
    # B column (KCL): A[n1,br] += -1, A[n2,br] += conj(n)
    # C row   (KVL): A[br,n1] += 1,  A[br,n2] += -n
    # C = B^H (Hermitian transpose) for complex turns ratios

    # From netlist 'T' elements: T<name> <n+> <n-> <turns_ratio> [phase=<rad>]
    for k, elem in enumerate(xfmr_list):
        branch_idx = xfmr_offset + k
        np_idx = node_map.idx(elem.nodes[0])
        nm_idx = node_map.idx(elem.nodes[1])
        ratio_mag = elem.value
        ratio_phase = elem.params.get('phase', 0.0)
        n_ratio = ratio_mag * np.exp(1j * ratio_phase)
        stamp_ideal_transformer(A, np_idx, nm_idx, n_ratio, branch_idx)

    # From PhasorSwitch objects (programmatic API)
    for k, ps in enumerate(phasor_switches):
        branch_idx = xfmr_offset + n_xfmr_netlist + k
        stamp_ideal_transformer(A, ps.from_bus, ps.to_bus, ps.turns_ratio, branch_idx)

    # --- Build excitation vector b(t) ----------------------------

    # Pre-build static current source stamps
    isrc_list = netlist.current_sources()
    isrc_info = []
    for elem in isrc_list:
        np_idx = node_map.idx(elem.nodes[0])
        nm_idx = node_map.idx(elem.nodes[1])
        func = elem.source_spec.time_function() if elem.source_spec else (lambda t: 0.0)
        isrc_info.append((np_idx, nm_idx, func))

    # Pre-build voltage source functions
    vsrc_funcs = []
    for elem in vsrc_list:
        func = elem.source_spec.time_function() if elem.source_spec else (lambda t: 0.0)
        vsrc_funcs.append(func)

    # Pre-allocate reusable buffer for b(t) to avoid np.zeros() per call.
    # Callers that store references across calls must use b_func_copy().
    _b_buf = np.zeros(n_total, dtype=_dtype)

    def b_func(t: float) -> np.ndarray:
        """Compute excitation vector b(t). Returns a SHARED buffer — do not store
        the reference if b_func will be called again before use."""
        b = _b_buf
        b[:] = 0.0  # reset (faster than np.zeros allocation)
        # Current sources: positive current flows from n+ to n-
        # In MNA sign convention, current INTO node is positive
        for np_idx, nm_idx, func in isrc_info:
            val = func(t)
            if np_idx >= 0:
                b[np_idx] -= val  # current leaves n+
            if nm_idx >= 0:
                b[nm_idx] += val  # current enters n-
        # Voltage sources: KVL row enforces V(n+) - V(n-) = vs(t).
        for k, func in enumerate(vsrc_funcs):
            b[vsrc_offset + k] = -func(t)
        return b

    # Build state labels
    state_labels = [f"V({node_map.name(i)})" for i in range(n_nodes)]
    state_labels += [f"I({name})" for name in vsrc_names]
    state_labels += [f"I({name})" for name in ind_names]
    state_labels += [f"I({name})" for name in xfmr_names]

    return MNASystem(
        A=A, E=E, b_func=b_func,
        n_nodes=n_nodes, n_vsrc=n_vsrc, n_ind=n_ind, n_total=n_total,
        node_map=node_map,
        n_xfmr=n_xfmr,
        vsrc_names=vsrc_names, ind_names=ind_names,
        xfmr_names=xfmr_names,
        state_labels=state_labels,
    )


# ----------------------------------------------------------------------
# NetlistCircuit: the main solver class
# ----------------------------------------------------------------------

class NetlistCircuit:
    """
    Generic circuit built from an LTspice netlist.

    Provides time-domain and phasor-domain simulation using MNA,
    compatible with the dynamic_phasor framework for comparison with
    LTspice results.

    Usage
    -----
    >>> from netlist_parser import parse_ltspice_netlist
    >>> from mna_circuit import NetlistCircuit
    >>>
    >>> # From a netlist string
    >>> netlist_text = '''
    ... * Series RLC
    ... V1 N001 0 SINE(0 1 92.3k)
    ... R1 N001 N002 3.0
    ... L1 N002 N003 100.04u
    ... C1 N003 0 30.07n
    ... R2 N003 0 2k
    ... .tran 0 0.2m
    ... .end
    ... '''
    >>> circuit = NetlistCircuit.from_string(netlist_text)
    >>> results = circuit.solve_time_domain()

    For phasor-domain simulation:
    >>> circuit.configure_phasor(omega_s=2*pi*92.3e3)
    >>> phasor_results = circuit.solve_phasor_domain()
    """

    def __init__(self, netlist: ParsedNetlist):
        """
        Build circuit from a parsed netlist.

        Parameters
        ----------
        netlist : ParsedNetlist
            Output from LTSpiceNetlistParser
        """
        self.netlist = netlist
        self.mna = build_mna(netlist)
        self.phasor: Optional[InstantaneousPhasor] = None
        self.omega_s: float = 0.0

        # Nonlinear element support (Phase 3)
        self._nonlinear_elements = []

        # Cache the decomposed system for ODE solving
        self._setup_ode_system()

    # File I/O removed for browser compatibility
    # @classmethod
    # def from_file(cls, filepath: str) -> 'NetlistCircuit':
    #     """Create circuit from a netlist file."""
    #     netlist = parse_ltspice_netlist(filepath)
    #     return cls(netlist)

    @classmethod
    def from_string(cls, text: str) -> 'NetlistCircuit':
        """Create circuit from a netlist string."""
        netlist = parse_ltspice_netlist(text)
        return cls(netlist)

    def _setup_ode_system(self):
        """
        Decompose E*dx/dt = A*x + b(t) into a proper ODE by eliminating
        algebraic variables.

        The MNA system is a semi-explicit index-1 DAE:
            E_d * dx_d/dt = A_dd * x_d + A_da * x_a + b_d(t)    (diff eqs)
            0             = A_ad * x_d + A_aa * x_a + b_a(t)    (alg eqs)

        From the algebraic equations:
            x_a = -A_aa^{-1} * (A_ad * x_d + b_a(t))

        Substituting into the differential equations gives a reduced ODE
        in only the differential state variables x_d.
        """
        E = self.mna.E
        A = self.mna.A
        n = self.mna.n_total

        # Identify differential (d) and algebraic (a) rows
        # A row i is algebraic if E[i,:] is all zeros
        self._diff_idx = []
        self._alg_idx = []
        for i in range(n):
            if np.any(np.abs(E[i, :]) > 1e-30):
                self._diff_idx.append(i)
            else:
                self._alg_idx.append(i)

        self._diff_idx = np.array(self._diff_idx, dtype=int)
        self._alg_idx = np.array(self._alg_idx, dtype=int)
        self._n_diff = len(self._diff_idx)
        self._n_alg = len(self._alg_idx)
        self._is_stiff = self._n_alg > 0
        # The fast block path below assumes E is block-diagonal (E_dd is its only
        # non-zero block) and E_dd is invertible. That holds for the vast
        # majority of circuits. It breaks for floating components (e.g. a
        # series capacitor whose two node voltages are both tagged differential
        # but only their difference is a true state), which make E_dd singular.
        # In that case we fall back to a general index-1 reduction that handles
        # a rank-deficient E. Default: fast path.
        self._general = False

        if self._n_alg == 0:
            # Pure ODE - all rows are differential (unless E itself is singular,
            # e.g. a floating-capacitor loop with no algebraic rows).
            if self._matrix_is_singular(E):
                self._setup_general_reduction(E, A)
                return
            self._E_inv = np.linalg.inv(E)
            self._M = self._E_inv @ A
            self._use_reduced = False
            return

        # Extract sub-matrices
        d = self._diff_idx
        a = self._alg_idx

        E_dd = E[np.ix_(d, d)]  # the only non-zero block when E is block-diagonal
        if self._matrix_is_singular(E_dd):
            # Rank-deficient differential block (floating component): the simple
            # subset-based reduction cannot represent this circuit. Use the
            # general coordinate-transform reduction instead.
            self._setup_general_reduction(E, A)
            return
        A_dd = A[np.ix_(d, d)]
        A_da = A[np.ix_(d, a)]
        A_ad = A[np.ix_(a, d)]
        A_aa = A[np.ix_(a, a)]

        # Check A_aa is invertible
        det_Aaa = np.linalg.det(A_aa)
        if abs(det_Aaa) < 1e-30:
            # If A_aa is singular, add small regularisation (GMIN in SPICE)
            gmin = 1e-12
            for i in range(len(a)):
                A_aa[i, i] += gmin

        self._E_dd = E_dd
        self._E_dd_inv = np.linalg.inv(E_dd)
        self._A_aa_inv = np.linalg.inv(A_aa)

        # Reduced system: E_dd * dx_d/dt = A_reduced * x_d + b_reduced(t)
        # where A_reduced = A_dd - A_da * A_aa^{-1} * A_ad
        self._A_reduced = A_dd - A_da @ self._A_aa_inv @ A_ad
        self._M_reduced = self._E_dd_inv @ self._A_reduced

        # For the excitation:
        # b_reduced(t) = b_d(t) - A_da * A_aa^{-1} * b_a(t)
        self._A_da = A_da
        self._A_ad = A_ad
        self._A_aa_inv_cached = self._A_aa_inv

        # Pre-compute composite matrices for hot-loop efficiency
        self._A_da_Aaa_inv = self._A_da @ self._A_aa_inv_cached
        self._E_dd_inv_A_da_Aaa_inv = self._E_dd_inv @ self._A_da_Aaa_inv

        self._use_reduced = True

    @staticmethod
    def _matrix_is_singular(M: np.ndarray, tol: float = None) -> bool:
        """Rank-deficiency test via singular values (robust for tiny matrices)."""
        if M.size == 0:
            return False
        s = np.linalg.svd(M, compute_uv=False)
        if tol is None:
            tol = max(M.shape) * np.finfo(float).eps * (s[0] if s.size else 0.0)
        return bool(np.count_nonzero(s > tol) < M.shape[0])

    def _setup_general_reduction(self, E: np.ndarray, A: np.ndarray) -> None:
        """General index-1 DAE reduction for a rank-deficient E.

        The simple subset reduction assumes the differential state is a subset
        of the circuit variables. A floating component (e.g. a series capacitor)
        breaks that: only a *combination* of node voltages is a true state. Here
        we change coordinates instead, via the SVD of E.

        With E = U S V^T (rank r), let z = V1^T x be the r genuine differential
        coordinates and w = V2^T x the algebraic ones (V = [V1 V2] orthogonal):

            S_r z' = U1^T A (V1 z + V2 w) + U1^T b      (differential, r eqs)
            0      = U2^T A (V1 z + V2 w) + U2^T b      (constraints, n-r eqs)

        Eliminating w from the constraints (index-1 requires U2^T A V2 invertible)
        gives a reduced ODE  z' = M_gen z + g(t)  and a linear map back to the
        full state x = V1 z + V2 w. M_gen and r are stored under the usual
        ``_M_reduced`` / ``_n_diff`` names so phasor configuration, the Jacobian
        build, and the integrators work unchanged; only excitation assembly and
        state reconstruction branch on ``self._general``.
        """
        n = E.shape[0]

        # --- Deflate structurally-empty states ----------------------------
        # Some MNA stampings allocate auxiliary state slots that end up wired
        # into no equation (e.g. the V(Lk) voltage variables for K-coupled
        # inductors in a series-compensated topology): a zero row AND zero
        # column in both E and A. They carry no physics and leave the pencil
        # (sE - A) singular, which makes the algebraic block below singular.
        # The block path tolerates them via the GMIN diagonal bump on A_aa;
        # here we drop them outright and pin them to 0 on reconstruction,
        # which is the same value GMIN regularisation gives.
        empty_tol = 1e-30
        keep = [j for j in range(n)
                if np.any(np.abs(E[:, j]) > empty_tol)
                or np.any(np.abs(A[:, j]) > empty_tol)
                or np.any(np.abs(E[j, :]) > empty_tol)
                or np.any(np.abs(A[j, :]) > empty_tol)]
        self._gen_keep = np.array(keep, dtype=int)
        self._gen_n_full = n
        if len(keep) < n:
            E = E[np.ix_(keep, keep)]
            A = A[np.ix_(keep, keep)]

        U, s, Vh = np.linalg.svd(E)
        tol = max(E.shape) * np.finfo(float).eps * (s[0] if s.size else 0.0)
        r = int(np.count_nonzero(s > tol))
        if r == 0:
            raise np.linalg.LinAlgError("E has rank 0; no dynamic states to reduce.")
        V = Vh.conj().T
        U1, U2 = U[:, :r], U[:, r:]
        V1, V2 = V[:, :r], V[:, r:]
        Sr_inv = np.diag(1.0 / s[:r])

        AV1 = A @ V1
        AV2 = A @ V2
        Aaa = U2.T @ AV2                      # (n-r, n-r) algebraic block
        if self._matrix_is_singular(Aaa):
            raise np.linalg.LinAlgError(
                "General DAE reduction failed: the algebraic block U2^T A V2 is "
                "singular (the circuit is higher-index or under-determined)."
            )
        Aaa_inv = np.linalg.inv(Aaa)
        AaV1 = U2.T @ AV1                      # (n-r, r)
        AdV1 = U1.T @ AV1                      # (r, r)
        AdV2 = U1.T @ AV2                      # (r, n-r)
        AdV2_Aaa_inv = AdV2 @ Aaa_inv

        # Reduced dynamics in the z-coordinates.
        M_gen = Sr_inv @ (AdV1 - AdV2_Aaa_inv @ AaV1)

        # Stored transforms for excitation / reconstruction.
        self._gen_U1, self._gen_U2 = U1, U2
        self._gen_V1, self._gen_V2 = V1, V2
        self._gen_Sr_inv = Sr_inv
        self._gen_Aaa_inv = Aaa_inv
        self._gen_AaV1 = AaV1
        self._gen_AdV2_Aaa_inv = AdV2_Aaa_inv

        # Present under the standard names so downstream config is untouched.
        self._M_reduced = M_gen
        self._n_diff = r
        self._n_alg = n - r
        self._is_stiff = True
        self._general = True
        self._use_reduced = True

    def _assert_not_general(self, what: str) -> None:
        """Guard solvers that don't yet support the general DAE reduction."""
        if getattr(self, "_general", False):
            raise NotImplementedError(
                f"{what} does not support circuits that need the general DAE "
                f"reduction (e.g. floating series capacitors). Use the adaptive "
                f"solve_time_domain / solve_phasor_domain path instead."
            )

    def _reduced_ic(self, x0_full: np.ndarray) -> np.ndarray:
        """Project a full initial state onto the differential coordinates."""
        if self._general:
            return self._gen_V1.T @ x0_full[self._gen_keep]
        return x0_full[self._diff_idx]

    def _reduced_g(self, b_full: np.ndarray) -> np.ndarray:
        """Reduced excitation g(t) so that  d(state)/dt = M_reduced @ state + g."""
        if self._general:
            b_kept = b_full[self._gen_keep]
            bd = self._gen_U1.T @ b_kept
            ba = self._gen_U2.T @ b_kept
            return self._gen_Sr_inv @ (bd - self._gen_AdV2_Aaa_inv @ ba)
        b_d = b_full[self._diff_idx]
        b_a = b_full[self._alg_idx]
        return self._E_dd_inv @ (b_d - self._A_da_Aaa_inv @ b_a)

    def _recover_full(self, z: np.ndarray, b_full: np.ndarray) -> np.ndarray:
        """Reconstruct the full state from differential coordinates.

        Accepts ``z`` as a vector (r,) or a stack (r, n_t); ``b_full`` shaped to
        match. Real or complex is inferred from the inputs.
        """
        if self._general:
            b_kept = b_full[self._gen_keep]
            ba = self._gen_U2.T @ b_kept
            w = -self._gen_Aaa_inv @ (self._gen_AaV1 @ z + ba)
            x_kept = self._gen_V1 @ z + self._gen_V2 @ w
            if len(self._gen_keep) == self._gen_n_full:
                return x_kept
            # Scatter back into the full state, pinning deflated slots to 0.
            shape = (self._gen_n_full,) if z.ndim == 1 else (self._gen_n_full, z.shape[1])
            x_full = np.zeros(shape, dtype=x_kept.dtype)
            x_full[self._gen_keep] = x_kept
            return x_full
        # Block path (kept for callers that route through this helper).
        if z.ndim == 1:
            x_full = np.zeros(self.mna.n_total, dtype=z.dtype)
            x_full[self._diff_idx] = z
            if self._n_alg > 0:
                b_a = b_full[self._alg_idx]
                x_full[self._alg_idx] = -self._A_aa_inv_cached @ (self._A_ad @ z + b_a)
            return x_full
        x_full = np.zeros((self.mna.n_total, z.shape[1]), dtype=z.dtype)
        x_full[self._diff_idx, :] = z
        if self._n_alg > 0:
            b_a = b_full[self._alg_idx]
            x_full[self._alg_idx, :] = -self._A_aa_inv_cached @ (self._A_ad @ z + b_a)
        return x_full

    def _recover_algebraic(self, x_d: np.ndarray, t: float) -> np.ndarray:
        """
        Recover algebraic variables from differential variables.
        x_a = -A_aa^{-1} * (A_ad * x_d + b_a(t))
        """
        b_full = self.mna.b_func(t)
        b_a = b_full[self._alg_idx]
        return -self._A_aa_inv_cached @ (self._A_ad @ x_d + b_a)

    def _recover_full_state(self, x_d: np.ndarray, t: float) -> np.ndarray:
        """Reconstruct full state vector from differential variables."""
        if self._general:
            return self._recover_full(x_d, self.mna.b_func(t))
        x_full = np.zeros(self.mna.n_total)
        x_full[self._diff_idx] = x_d
        if self._n_alg > 0:
            x_full[self._alg_idx] = self._recover_algebraic(x_d, t)
        return x_full

    def _compute_steady_state_ic(self) -> np.ndarray:
        """
        Compute sinusoidal steady-state initial condition for EMT simulation.

        Uses phasor-domain solve: x_phasor = -M_dp^{-1} * g
        Returns x(0) = Re[x_phasor] (the real part at t=0).

        Calls configure_phasor() internally if not already configured.
        """
        if self.phasor is None:
            self.configure_phasor()

        b_phasor_func = self._build_phasor_b_func()
        b0 = b_phasor_func(0.0)

        if self._use_reduced:
            g = self._reduced_g(b0)
            x_d_ss = np.linalg.solve(-self._M_dp, g)
            x_full = self._recover_full(x_d_ss, b0)
        else:
            g = self._E_inv @ b0
            x_full = np.linalg.solve(-self._M_dp_full, g)

        return np.real(x_full)

    # ----------------------------------------------------------
    # Nonlinear element support
    # ----------------------------------------------------------

    @property
    def has_nonlinear(self) -> bool:
        """True if any nonlinear elements have been registered."""
        return len(self._nonlinear_elements) > 0

    def add_nonlinear_element(self, element) -> None:
        """Register a nonlinear element for NR-augmented solving.

        Parameters
        ----------
        element : NonlinearElement
            Must implement evaluate(v_nodes, t) -> (i_inj, J_inj).
        """
        self._nonlinear_elements.append(element)

    def _eval_nonlinear(self, v_nodes: np.ndarray, t: float
                        ) -> Tuple[np.ndarray, np.ndarray]:
        """Evaluate all nonlinear elements and sum contributions.

        Parameters
        ----------
        v_nodes : ndarray (n_nodes,)
            Current node voltage estimates.
        t : float
            Current time.

        Returns
        -------
        i_inj_total : ndarray (n_nodes,)
            Total nonlinear current injection into KCL equations.
        J_inj_total : ndarray (n_nodes, n_nodes)
            Total Jacobian di_inj/dv.
        """
        n = self.mna.n_nodes
        i_total = np.zeros(n)
        J_total = np.zeros((n, n))
        for elem in self._nonlinear_elements:
            i_inj, J_inj = elem.evaluate(v_nodes, t)
            i_total += i_inj
            J_total += J_inj
        return i_total, J_total

    def _eval_nonlinear_phasor(self, v_phasor: np.ndarray,
                               t: float) -> Tuple[np.ndarray, np.ndarray]:
        """Evaluate all nonlinear elements in phasor domain.

        Parameters
        ----------
        v_phasor : complex ndarray (n_nodes,)
            Complex voltage phasor at each node.
        t : float
            Current time.

        Returns
        -------
        i_inj_total : complex ndarray (n_nodes,)
            Total phasor current injection.
        J_inj_total : complex ndarray (n_nodes, n_nodes)
            Total linearised admittance Jacobian.
        """
        n = self.mna.n_nodes
        i_total = np.zeros(n, dtype=complex)
        J_total = np.zeros((n, n), dtype=complex)
        for elem in self._nonlinear_elements:
            i_inj, J_inj = elem.evaluate_phasor(v_phasor, t)
            i_total += i_inj
            J_total += J_inj
        return i_total, J_total

    # ----------------------------------------------------------
    # Phasor configuration
    # ----------------------------------------------------------

    def configure_phasor(self, omega_s: float = None,
                         omega_0: float = None,
                         omega_1: float = None,
                         alpha: float = 0.0):
        """
        Configure phasor transformation.

        If omega_s is not given, tries to auto-detect from the first
        SINE voltage source in the netlist.

        Parameters
        ----------
        omega_s : float, optional
            Carrier angular frequency (rad/s)
        omega_0, omega_1, alpha : float
            FM parameters (see PhasorConfig)
        """
        if omega_s is None:
            omega_s = self._detect_carrier_frequency()

        self.omega_s = omega_s
        config = PhasorConfig(
            omega=omega_s,
            omega_0=omega_0 or omega_s,
            omega_1=omega_1 or 0,
            alpha=alpha,
        )
        self.phasor = InstantaneousPhasor(config)

        # Pre-compute the phasor-modified system matrix M_dp = M_reduced - j*omega*I
        # so that phasor_domain_ode becomes: dx_d = M_dp @ x_d + E_dd_inv @ b_reduced
        if self._use_reduced:
            nd = self._n_diff
            self._M_dp = self._M_reduced - 1j * omega_s * np.eye(nd)
        else:
            self._M_dp_full = self._E_inv @ (self.mna.A - 1j * omega_s * self.mna.E)

    def _detect_carrier_frequency(self) -> float:
        """Auto-detect carrier frequency from SINE or PULSE sources."""
        for elem in self.netlist.voltage_sources():
            if elem.source_spec and elem.source_spec.source_type == SourceType.SINE:
                return elem.source_spec.omega()
        for elem in self.netlist.current_sources():
            if elem.source_spec and elem.source_spec.source_type == SourceType.SINE:
                return elem.source_spec.omega()
        # Try PULSE sources: use fundamental frequency = 1/period
        for elem in self.netlist.voltage_sources():
            if elem.source_spec and elem.source_spec.source_type == SourceType.PULSE:
                period = elem.source_spec.pulse_period
                if period > 0:
                    return 2 * np.pi / period
        for elem in self.netlist.current_sources():
            if elem.source_spec and elem.source_spec.source_type == SourceType.PULSE:
                period = elem.source_spec.pulse_period
                if period > 0:
                    return 2 * np.pi / period
        raise ValueError("No SINE/PULSE source found; please specify omega_s manually")

    # ----------------------------------------------------------
    # Time-domain solver
    # ----------------------------------------------------------

    def time_domain_ode(self, t: float, x_d: np.ndarray) -> np.ndarray:
        """
        ODE right-hand side for the reduced differential system.

        If no algebraic elimination was needed:
            dx/dt = E_inv @ (A*x + b(t))
        If reduced:
            dx_d/dt = E_dd_inv @ (A_reduced * x_d + b_d(t) - A_da * A_aa^{-1} * b_a(t))
        """
        if not self._use_reduced:
            return self._M @ x_d + self._E_inv @ self.mna.b_func(t)

        b_full = self.mna.b_func(t)
        return self._M_reduced @ x_d + self._reduced_g(b_full)

    def solve_time_domain(self, t_span: Tuple[float, float] = None,
                          t_eval: np.ndarray = None,
                          x0: np.ndarray = None,
                          **solver_kwargs) -> Dict:
        """
        Solve the circuit in the time domain.

        Parameters
        ----------
        t_span : tuple (t_start, t_end), optional
            If None, uses .tran command from netlist
        t_eval : ndarray, optional
            Specific times to evaluate
        x0 : ndarray, optional
            Initial state vector. If None, uses .ic or zeros
        **solver_kwargs :
            Additional arguments for scipy.integrate.solve_ivp

        Returns
        -------
        dict
            't' : time vector
            'x' : full state matrix (n_total x n_points)
            'node_voltages' : dict {node_name: voltage_array}
            'branch_currents' : dict {branch_name: current_array}
            For each voltage source: 'I(V1)', etc.
            For each inductor: 'I(L1)', etc.
            For each node: 'V(N001)', etc.
        """
        # Determine time span
        if t_span is None:
            tran = self.netlist.tran_params()
            if tran:
                t_start = tran.get('t_start', 0.0)
                t_stop = tran['t_stop']
                t_span = (t_start, t_stop)
            else:
                raise ValueError("No .tran command found; provide t_span")

        # Initial conditions
        if x0 is None:
            x0_full = np.zeros(self.mna.n_total)
            # Apply .ic
            for node_name, val in self.netlist.initial_conditions.items():
                idx = self.mna.node_map.idx(node_name)
                if idx >= 0:
                    x0_full[idx] = val
            # Apply IC= on components
            for k, elem in enumerate(self.netlist.inductors()):
                if elem.ic is not None:
                    x0_full[self.mna.n_nodes + self.mna.n_vsrc + k] = elem.ic
            for elem in self.netlist.capacitors():
                if elem.ic is not None:
                    np_idx = self.mna.node_map.idx(elem.nodes[0])
                    _nm_idx = self.mna.node_map.idx(elem.nodes[1])
                    if np_idx >= 0:
                        x0_full[np_idx] = elem.ic
        else:
            x0_full = x0

        # Extract differential initial conditions
        if self._use_reduced:
            x0_d = self._reduced_ic(x0_full)
        else:
            x0_d = x0_full

        # Time eval points
        if t_eval is None:
            # Aim for ~200 points per carrier period if there's a SINE source
            try:
                omega = self._detect_carrier_frequency()
                period = 2 * np.pi / omega
                n_periods = (t_span[1] - t_span[0]) / period
                n_points = max(2000, min(int(200 * n_periods), 100000))
            except ValueError:
                n_points = 5000
            t_eval = np.linspace(t_span[0], t_span[1], n_points)

        # Solve - use implicit method if stiff
        default_method = 'Radau' if self._is_stiff else 'RK45'
        sol = solve_ivp(
            self.time_domain_ode,
            t_span, x0_d, t_eval=t_eval,
            method=solver_kwargs.pop('method', default_method),
            rtol=solver_kwargs.pop('rtol', 1e-8),
            atol=solver_kwargs.pop('atol', 1e-10),
            **solver_kwargs,
        )

        if not sol.success:
            warnings.warn(f"ODE solver warning: {sol.message}")

        # Reconstruct full state at each time point
        if self._use_reduced:
            b_full_ts = np.column_stack([self.mna.b_func(t) for t in sol.t])
            x_full = self._recover_full(sol.y, b_full_ts)
        else:
            x_full = sol.y

        results = self._package_results(sol.t, x_full)
        results["solver_stats"] = {
            "nfev": int(sol.nfev),
            "njev": int(getattr(sol, "njev", 0)),
            "nlu": int(getattr(sol, "nlu", 0)),
        }
        return results

    def _package_results(self, t: np.ndarray, x: np.ndarray) -> Dict:
        """Package raw ODE solution into a labelled dictionary."""
        mna = self.mna
        results = {'t': t, 'x': x}

        # Node voltages
        node_voltages = {}
        for i in range(mna.n_nodes):
            name = mna.node_map.name(i)
            key = f"V({name})"
            node_voltages[name] = x[i]
            results[key] = x[i]
        results['node_voltages'] = node_voltages

        # Branch currents
        branch_currents = {}
        for k, name in enumerate(mna.vsrc_names):
            key = f"I({name})"
            branch_currents[name] = x[mna.n_nodes + k]
            results[key] = x[mna.n_nodes + k]
        for k, name in enumerate(mna.ind_names):
            key = f"I({name})"
            branch_currents[name] = x[mna.n_nodes + mna.n_vsrc + k]
            results[key] = x[mna.n_nodes + mna.n_vsrc + k]
        results['branch_currents'] = branch_currents

        # Source voltage waveform for convenience (lazily computed on access)
        # Precomputing for all sources is expensive (~14ms for 118-bus), so
        # we store the functions and only compute when needed.
        vsrc_specs = {}
        for elem in self.netlist.voltage_sources():
            if elem.source_spec:
                vsrc_specs[elem.name] = elem.source_spec.time_function()
        results['_source_voltage_funcs'] = vsrc_specs
        results['source_voltages'] = {}

        return results

    # ----------------------------------------------------------
    # Fixed-step EMT solver
    # ----------------------------------------------------------

    def _should_use_sparse(self, n_states, threshold=50):
        """Use sparse when matrix is large enough to benefit."""
        return n_states > threshold

    def solve_time_domain_fixed(self, t_span=None, n_steps=None, x0=None,
                                 steps_per_period: int = 200,
                                 init_steady_state: bool = False,
                                 use_sparse: str = 'auto',
                                 nr_tol: float = 1e-6,
                                 nr_max_iter: int = 20,
                                 use_cpp=None) -> Dict:
        """
        Solve EMT using fixed-step trapezoidal rule with pre-factorised LU.

        Much faster than scipy adaptive solver for LTI circuits (~500-3000x).
        Uses real-valued arithmetic (unlike phasor-domain which is complex).

        When nonlinear elements are registered, uses Newton-Raphson inner
        loop at each time step. For linear circuits (no nonlinear elements),
        the NR code is never entered — zero overhead.

        Parameters
        ----------
        t_span : tuple, optional
        n_steps : int, optional
            Number of time steps. Overrides steps_per_period if given.
        x0 : ndarray, optional
        steps_per_period : int
            Steps per carrier period for auto step-count. Default 200.
        init_steady_state : bool
            If True, initialise with sinusoidal steady-state solution.
        nr_tol : float
            Newton-Raphson convergence tolerance (max absolute update).
        nr_max_iter : int
            Maximum NR iterations per time step.

        Returns
        -------
        dict - same format as solve_time_domain
        """
        self._assert_not_general("solve_time_domain_fixed")
        # Time span
        if t_span is None:
            tran = self.netlist.tran_params()
            if tran:
                t_span = (tran.get('t_start', 0.0), tran['t_stop'])
            else:
                raise ValueError("No .tran command; provide t_span")

        # Step count
        if n_steps is None:
            try:
                omega = self._detect_carrier_frequency()
                period = 2 * np.pi / omega
                n_periods = (t_span[1] - t_span[0]) / period
                n_steps = max(200, int(steps_per_period * n_periods))
            except ValueError:
                n_steps = 5000

        dt = (t_span[1] - t_span[0]) / n_steps
        t_arr = np.linspace(t_span[0], t_span[1], n_steps + 1)

        # Initial conditions
        if init_steady_state and x0 is None:
            x0_full = self._compute_steady_state_ic()
        elif x0 is not None:
            x0_full = x0
        else:
            x0_full = np.zeros(self.mna.n_total)
            for node_name, val in self.netlist.initial_conditions.items():
                idx = self.mna.node_map.idx(node_name)
                if idx >= 0:
                    x0_full[idx] = val
            for k, elem in enumerate(self.netlist.inductors()):
                if elem.ic is not None:
                    x0_full[self.mna.n_nodes + self.mna.n_vsrc + k] = elem.ic
            for elem in self.netlist.capacitors():
                if elem.ic is not None:
                    np_idx = self.mna.node_map.idx(elem.nodes[0])
                    if np_idx >= 0:
                        x0_full[np_idx] = elem.ic

        # Branch: nonlinear vs linear path (checked ONCE before hot loop)
        if self._nonlinear_elements:
            return self._solve_fixed_nonlinear(
                t_arr, dt, n_steps, x0_full, nr_tol, nr_max_iter)

        # === LINEAR FAST PATH (original code, zero NR overhead) ===
        # Determine sparse vs dense
        n_eff = self._n_diff if self._use_reduced else self.mna.n_total
        if use_sparse == 'auto':
            _sparse = self._should_use_sparse(n_eff)
        else:
            _sparse = bool(use_sparse)

        # C++ acceleration: use for dense paths when available
        # Guard: C++ needs n_diff > 0 (DC-only circuits have no diff vars)
        _use_cpp = use_cpp if use_cpp is not None else (HAS_CPP and not _sparse)
        _use_cpp = _use_cpp and HAS_CPP
        if _use_cpp and self._use_reduced and self._n_diff == 0:
            _use_cpp = False

        if _use_cpp:
            x_full = self._solve_emt_cpp(t_arr, dt, n_steps, x0_full)
        elif self._use_reduced:
            nd = self._n_diff
            M = self._M_reduced

            diff_idx = self._diff_idx
            alg_idx = self._alg_idx
            b_func = self.mna.b_func

            if _sparse:
                import scipy.sparse as sp
                import scipy.sparse.linalg as spla
                M_sp = sp.csc_matrix(M)
                I_sp = sp.eye(nd, format='csc')
                LHS_sp = I_sp - (dt / 2) * M_sp
                RHS_sp = I_sp + (dt / 2) * M_sp
                lu_obj = spla.splu(LHS_sp)
                E_dd_inv_sp = sp.csc_matrix(self._E_dd_inv)
                E_dd_inv_A_da_Aaa_inv_sp = sp.csc_matrix(self._E_dd_inv_A_da_Aaa_inv)
                A_aa_inv_sp = sp.csc_matrix(self._A_aa_inv_cached)
                A_ad_sp = sp.csc_matrix(self._A_ad)

                def g_func_with_ba_sp(t_val):
                    b_full = b_func(t_val)
                    b_d = b_full[diff_idx]
                    b_a = b_full[alg_idx]
                    g = E_dd_inv_sp.dot(b_d) - E_dd_inv_A_da_Aaa_inv_sp.dot(b_a)
                    return g, b_a

                x_d = x0_full[diff_idx]  # no copy needed: fancy indexing creates a new array
                n_total = self.mna.n_total
                x_full = np.zeros((n_total, n_steps + 1))

                g_n, b_a_n = g_func_with_ba_sp(t_arr[0])
                x_a = -A_aa_inv_sp.dot(A_ad_sp.dot(x_d) + b_a_n)
                x_full[diff_idx, 0] = x_d
                x_full[alg_idx, 0] = x_a

                for i in range(n_steps):
                    g_n1, b_a_n1 = g_func_with_ba_sp(t_arr[i + 1])
                    rhs = RHS_sp.dot(x_d) + (dt / 2) * (g_n + g_n1)
                    x_d = lu_obj.solve(rhs)
                    g_n = g_n1
                    x_a = -A_aa_inv_sp.dot(A_ad_sp.dot(x_d) + b_a_n1)
                    x_full[diff_idx, i + 1] = x_d
                    x_full[alg_idx, i + 1] = x_a
            else:
                from scipy.linalg import lu_factor, lu_solve
                I_nd = np.eye(nd)
                LHS = I_nd - (dt / 2) * M
                RHS_mat = I_nd + (dt / 2) * M
                lu, piv = lu_factor(LHS)

                x_d = x0_full[self._diff_idx]  # no copy needed: fancy indexing creates a new array
                E_dd_inv = self._E_dd_inv
                E_dd_inv_A_da_Aaa_inv = self._E_dd_inv_A_da_Aaa_inv
                A_aa_inv_cached = self._A_aa_inv_cached
                A_ad = self._A_ad

                def g_func_with_ba(t_val):
                    b_full = b_func(t_val)
                    b_d = b_full[diff_idx]
                    b_a = b_full[alg_idx]
                    g = E_dd_inv @ b_d - E_dd_inv_A_da_Aaa_inv @ b_a
                    return g, b_a

                n_total = self.mna.n_total
                x_full = np.zeros((n_total, n_steps + 1))

                g_n, b_a_n = g_func_with_ba(t_arr[0])
                x_a = -A_aa_inv_cached @ (A_ad @ x_d + b_a_n)
                x_full[diff_idx, 0] = x_d
                x_full[alg_idx, 0] = x_a

                for i in range(n_steps):
                    g_n1, b_a_n1 = g_func_with_ba(t_arr[i + 1])
                    rhs = RHS_mat @ x_d + (dt / 2) * (g_n + g_n1)
                    x_d = lu_solve((lu, piv), rhs)
                    g_n = g_n1

                    x_a = -A_aa_inv_cached @ (A_ad @ x_d + b_a_n1)
                    x_full[diff_idx, i + 1] = x_d
                    x_full[alg_idx, i + 1] = x_a

        else:
            n = self.mna.n_total
            M = self._M

            if _sparse:
                import scipy.sparse as sp
                import scipy.sparse.linalg as spla
                M_sp = sp.csc_matrix(M)
                I_sp = sp.eye(n, format='csc')
                LHS_sp = I_sp - (dt / 2) * M_sp
                RHS_sp = I_sp + (dt / 2) * M_sp
                lu_obj = spla.splu(LHS_sp)

                x_c = x0_full  # no copy needed: x_c is reassigned, not mutated in-place
                x_full = np.zeros((n, n_steps + 1))
                x_full[:, 0] = x_c

                E_inv_sp = sp.csc_matrix(self._E_inv)
                b_func = self.mna.b_func
                g_n = E_inv_sp.dot(b_func(t_arr[0]))

                for i in range(n_steps):
                    g_n1 = E_inv_sp.dot(b_func(t_arr[i + 1]))
                    rhs = RHS_sp.dot(x_c) + (dt / 2) * (g_n + g_n1)
                    x_c = lu_obj.solve(rhs)
                    g_n = g_n1
                    x_full[:, i + 1] = x_c
            else:
                from scipy.linalg import lu_factor, lu_solve
                I_n = np.eye(n)
                LHS = I_n - (dt / 2) * M
                RHS_mat = I_n + (dt / 2) * M
                lu, piv = lu_factor(LHS)

                x_c = x0_full  # no copy needed: x_c is reassigned, not mutated in-place
                x_full = np.zeros((n, n_steps + 1))
                x_full[:, 0] = x_c

                E_inv = self._E_inv
                b_func = self.mna.b_func
                g_n = E_inv @ b_func(t_arr[0])

                for i in range(n_steps):
                    g_n1 = E_inv @ b_func(t_arr[i + 1])
                    rhs = RHS_mat @ x_c + (dt / 2) * (g_n + g_n1)
                    x_c = lu_solve((lu, piv), rhs)
                    g_n = g_n1
                    x_full[:, i + 1] = x_c

        results = self._package_results(t_arr, x_full)
        results["solver_stats"] = {
            "nfev": n_steps,
            "njev": 0,
            "nlu": 1,
            "n_steps": n_steps,
            "dt": dt,
            "backend": "cpp" if _use_cpp else "python",
        }
        return results

    def _solve_emt_cpp(self, t_arr, dt, n_steps, x0_full):
        """EMT solve via C++ backend — uses scipy LU for identical results."""
        from scipy.linalg import lu_factor
        b_func = self.mna.b_func

        if self._use_reduced:
            nd = self._n_diff
            M = self._M_reduced
            diff_idx = self._diff_idx
            alg_idx = self._alg_idx
            E_dd_inv = self._E_dd_inv
            E_dd_inv_A_da_Aaa_inv = self._E_dd_inv_A_da_Aaa_inv
            A_aa_inv_cached = self._A_aa_inv_cached
            A_ad = self._A_ad

            # Build LHS and RHS_mat (same as Python path)
            I_nd = np.eye(nd)
            LHS = I_nd - (dt / 2) * M
            RHS_mat = np.ascontiguousarray(I_nd + (dt / 2) * M, dtype=np.float64)
            lu_data, piv = lu_factor(LHS)
            # lu_data is Fortran-order; piv is 0-based int32

            # Pre-compute g_all and b_a_all
            g_all = np.empty((n_steps + 1, nd), dtype=np.float64)
            n_alg = len(alg_idx)
            b_a_all = np.empty((n_steps + 1, n_alg), dtype=np.float64)
            for i in range(n_steps + 1):
                b_full = b_func(t_arr[i])
                b_d = b_full[diff_idx]
                b_a = b_full[alg_idx]
                g_all[i] = E_dd_inv @ b_d - E_dd_inv_A_da_Aaa_inv @ b_a
                b_a_all[i] = b_a

            x0_d = np.ascontiguousarray(x0_full[diff_idx], dtype=np.float64)

            # C++ tight loop using scipy's LU factorization
            X_d = dpspice_cpp.emt_trapezoidal_solve(
                RHS_mat, np.asfortranarray(lu_data),
                piv.astype(np.int32), g_all, x0_d, dt)

            # Reconstruct algebraic vars (vectorized)
            n_total = self.mna.n_total
            x_full = np.zeros((n_total, n_steps + 1))
            x_full[diff_idx, :] = X_d.T
            x_full[alg_idx, :] = -(A_aa_inv_cached @ (A_ad @ X_d.T + b_a_all.T))

        else:
            n = self.mna.n_total
            M = self._M
            E_inv = self._E_inv

            I_n = np.eye(n)
            LHS = I_n - (dt / 2) * M
            RHS_mat = np.ascontiguousarray(I_n + (dt / 2) * M, dtype=np.float64)
            lu_data, piv = lu_factor(LHS)

            # Pre-compute g_all
            g_all = np.empty((n_steps + 1, n), dtype=np.float64)
            for i in range(n_steps + 1):
                g_all[i] = E_inv @ b_func(t_arr[i])

            x0 = np.ascontiguousarray(x0_full, dtype=np.float64)

            X = dpspice_cpp.emt_trapezoidal_solve(
                RHS_mat, np.asfortranarray(lu_data),
                piv.astype(np.int32), g_all, x0, dt)
            x_full = X.T

        return x_full

    def _solve_fixed_nonlinear(self, t_arr, dt, n_steps, x0_full,
                                nr_tol, nr_max_iter):
        """NR-augmented fixed-step trapezoidal solver for nonlinear elements.

        Uses the full MNA system with SPICE-style companion model NR.
        At each NR iteration, the nonlinear element is linearised as a
        Norton equivalent (conductance + current source) and folded into
        the system matrix.

        Trapezoidal discretisation:
            (E/dt - A/2) * x_{n+1} = (E/dt + A/2) * x_n
                + 0.5*(b_{n+1} + b_n) + 0.5*(f_nl_n + f_nl_{n+1})
        """
        from scipy.linalg import lu_factor, lu_solve

        E = self.mna.E
        A = self.mna.A
        n = self.mna.n_total
        n_nodes = self.mna.n_nodes
        b_func = self.mna.b_func

        # Base trapezoidal companion matrices (linear part)
        G_base = E / dt - A / 2.0
        H_mat = E / dt + A / 2.0

        x_full_out = np.zeros((n, n_steps + 1))
        x_full_out[:, 0] = x0_full
        x_cur = x0_full.copy()

        b_prev = b_func(t_arr[0]).copy()  # copy: stored across b_func calls

        # Evaluate NL at initial state
        v_prev = x_cur[:n_nodes]
        i_nl_prev, _ = self._eval_nonlinear(v_prev, t_arr[0])
        f_nl_prev = np.zeros(n)
        f_nl_prev[:n_nodes] = i_nl_prev

        total_nr_iters = 0

        for i in range(n_steps):
            t_next = t_arr[i + 1]
            b_next = b_func(t_next).copy()  # copy: b_prev still needed

            # Constant part of RHS for this step (uses previous NL eval)
            rhs_const = H_mat @ x_cur + 0.5 * (b_next + b_prev) \
                + 0.5 * f_nl_prev

            # NR iteration: use previous solution as predictor
            x_k = x_cur.copy()
            converged = False

            for k in range(nr_max_iter):
                # Evaluate NL at current iterate
                v_k = x_k[:n_nodes]
                i_nl_k, J_nl_k = self._eval_nonlinear(v_k, t_next)

                # SPICE companion model: linearise NL as Norton equivalent
                # i_nl(v) ≈ i_nl(v_k) + J_nl*(v - v_k)
                #          = J_nl*v + (i_nl(v_k) - J_nl*v_k)
                # Norton current: i_eq = i_nl(v_k) - J_nl*v_k
                i_eq = i_nl_k - J_nl_k @ v_k

                # Build effective system with NL companion
                # G_eff = G_base - 0.5 * J_nl_full (NL Jacobian in node block)
                G_eff = G_base.copy()
                G_eff[:n_nodes, :n_nodes] -= 0.5 * J_nl_k

                # Effective RHS = rhs_const + 0.5 * f_nl_equiv(x_{n+1})
                # where f_nl_equiv = J_nl*v + i_eq (in node rows)
                # But the J_nl*v part is already in G_eff, so we add i_eq
                rhs_eff = rhs_const.copy()
                rhs_eff[:n_nodes] += 0.5 * i_eq

                # Solve the companion system
                lu_eff, piv_eff = lu_factor(G_eff)
                x_new = lu_solve((lu_eff, piv_eff), rhs_eff)

                # Convergence check: max voltage update
                max_update = np.max(np.abs(x_new - x_k))
                x_k = x_new

                if max_update < nr_tol:
                    converged = True
                    total_nr_iters += k + 1
                    break

            if not converged:
                total_nr_iters += nr_max_iter
                warnings.warn(
                    f"NR did not converge at step {i} (t={t_next:.6f}), "
                    f"update={max_update:.2e}")

            # Store result
            x_cur = x_k
            x_full_out[:, i + 1] = x_cur
            b_prev = b_next

            # Update NL state for next step
            f_nl_prev = np.zeros(n)
            f_nl_prev[:n_nodes] = i_nl_k

        results = self._package_results(t_arr, x_full_out)
        results["solver_stats"] = {
            "nfev": n_steps,
            "njev": 0,
            "nlu": 1,
            "n_steps": n_steps,
            "dt": dt,
            "nr_total_iters": total_nr_iters,
            "nr_avg_iters": total_nr_iters / max(n_steps, 1),
        }
        return results

    def _solve_phasor_fixed_nonlinear(self, t_arr, dt, n_steps, x0_full,
                                        b_phasor_func,
                                        nr_tol=1e-6, nr_max_iter=10):
        """NR-augmented DP solver for phasor-domain nonlinear elements.

        Uses the full MNA system (not reduced) with complex trapezoidal rule.
        Structure mirrors _solve_fixed_nonlinear but with complex arithmetic
        and the DP frequency shift (A → A - jωE).

        Each nonlinear element provides an equivalent admittance Jacobian via
        evaluate_phasor(), enabling NR convergence in 1-2 iterations for
        slowly-varying phasor envelopes.
        """
        from scipy.linalg import lu_factor, lu_solve

        E = self.mna.E.astype(complex)
        A = self.mna.A.astype(complex)
        omega_s = self.omega_s
        n = self.mna.n_total
        n_nodes = self.mna.n_nodes

        # DP companion matrices (complex): A_dp = A - jωE
        A_dp = A - 1j * omega_s * E
        G_base = E / dt - A_dp / 2.0
        H_mat = E / dt + A_dp / 2.0

        x_full_out = np.zeros((n, n_steps + 1), dtype=complex)
        x_full_out[:, 0] = x0_full
        x_cur = x0_full.copy()

        b_prev = b_phasor_func(t_arr[0])

        # Evaluate NL at initial state
        v_prev = x_cur[:n_nodes]
        i_nl_prev, _ = self._eval_nonlinear_phasor(v_prev, t_arr[0])
        f_nl_prev = np.zeros(n, dtype=complex)
        f_nl_prev[:n_nodes] = i_nl_prev

        total_nr_iters = 0

        for i in range(n_steps):
            t_next = t_arr[i + 1]
            b_next = b_phasor_func(t_next)

            rhs_const = H_mat @ x_cur + 0.5 * (b_next + b_prev) \
                + 0.5 * f_nl_prev

            x_k = x_cur.copy()
            converged = False

            for k in range(nr_max_iter):
                v_k = x_k[:n_nodes]
                i_nl_k, J_nl_k = self._eval_nonlinear_phasor(v_k, t_next)

                # Norton linearisation (complex equivalent admittance)
                i_eq = i_nl_k - J_nl_k @ v_k

                G_eff = G_base.copy()
                G_eff[:n_nodes, :n_nodes] -= 0.5 * J_nl_k

                rhs_eff = rhs_const.copy()
                rhs_eff[:n_nodes] += 0.5 * i_eq

                lu_eff, piv_eff = lu_factor(G_eff)
                x_new = lu_solve((lu_eff, piv_eff), rhs_eff)

                max_update = float(np.max(np.abs(x_new - x_k)))
                x_k = x_new

                if max_update < nr_tol:
                    converged = True
                    total_nr_iters += k + 1
                    break

            if not converged:
                total_nr_iters += nr_max_iter
                warnings.warn(
                    f"Phasor NR did not converge at step {i} (t={t_next:.6f}), "
                    f"update={max_update:.2e}")

            x_cur = x_k
            x_full_out[:, i + 1] = x_cur
            b_prev = b_next

            f_nl_prev = np.zeros(n, dtype=complex)
            f_nl_prev[:n_nodes] = i_nl_k

        results = self._package_phasor_results(t_arr, x_full_out)
        results["solver_stats"] = {
            "nfev": n_steps,
            "njev": 0,
            "nlu": 1,
            "n_steps": n_steps,
            "dt": dt,
            "nr_total_iters": total_nr_iters,
            "nr_avg_iters": total_nr_iters / max(n_steps, 1),
        }
        return results

    # ----------------------------------------------------------
    # Phasor-domain solver
    # ----------------------------------------------------------

    def _build_phasor_b_func(self) -> Callable[[float], np.ndarray]:
        """
        Build the phasor-domain excitation vector.

        For each source, convert its time-domain waveform to phasor form:
          SINE source: v_tilde_s(t) = V_amp * e^(j*phi_s) (constant phasor for
                       standard case where theta(t) = omega_s*t)
          DC source:   contributes a rotating phasor at -omega_s
          PULSE/PWL:   use instantaneous phasor transform
        """
        mna = self.mna
        phasor = self.phasor
        omega_s = self.omega_s

        # Pre-compute phasor source functions
        vsrc_phasor_funcs = []
        for elem in self.netlist.voltage_sources():
            spec = elem.source_spec
            if spec is None:
                vsrc_phasor_funcs.append(lambda t: 0.0 + 0.0j)
                continue

            if spec.source_type == SourceType.SINE:
                # For SINE(Voff, Vamp, freq, td, theta, phi):
                # If freq matches omega_s, the phasor is constant
                amp = spec.sine_amplitude
                phi_rad = np.radians(spec.sine_phase)
                offset = spec.sine_offset
                freq = spec.sine_freq
                td = spec.sine_delay
                omega_src = 2 * np.pi * freq

                if abs(omega_src - omega_s) < 1.0:
                    # Source at carrier frequency - constant phasor
                    # v(t) = Voff + Vamp*sin(omega*t + phi)
                    #       = Voff + Vamp*cos(omega*t + phi - pi/2)
                    # Phasor of cos part: Vamp * e^(j(phi - pi/2))
                    # Note: SPICE SINE uses sin(), our phasor uses cos() as reference
                    phasor_val = amp * np.exp(1j * (phi_rad - np.pi / 2))

                    def _sine_phasor(t, pv=phasor_val, off=offset, td_=td):
                        if t < td_:
                            return 0.0 + 0.0j
                        # The offset contributes a rotating term in phasor domain
                        # For simplicity and typical use, offset is usually 0
                        return pv
                    vsrc_phasor_funcs.append(_sine_phasor)
                else:
                    # Source frequency != carrier - need instantaneous transform
                    time_func = spec.time_function()
                    def _general_phasor(t, f=time_func, p=phasor):
                        val = f(t)
                        theta = p.theta(t)
                        return val * np.exp(-1j * theta)
                    vsrc_phasor_funcs.append(_general_phasor)

            elif spec.source_type == SourceType.DC:
                # DC in phasor domain: rotating at -omega_s
                dc = spec.dc_value
                def _dc_phasor(t, dc_=dc, ws=omega_s):
                    return dc_ * np.exp(-1j * ws * t)
                vsrc_phasor_funcs.append(_dc_phasor)

            else:
                # General case: instantaneous phasor transform
                time_func = spec.time_function()
                def _gen_phasor(t, f=time_func, p=phasor):
                    val = f(t)
                    theta = p.theta(t)
                    return val * np.exp(-1j * theta)
                vsrc_phasor_funcs.append(_gen_phasor)

        # Current source phasors
        isrc_list = self.netlist.current_sources()
        isrc_phasor_info = []
        for elem in isrc_list:
            np_idx = mna.node_map.idx(elem.nodes[0])
            nm_idx = mna.node_map.idx(elem.nodes[1])
            spec = elem.source_spec
            if spec and spec.source_type == SourceType.SINE:
                amp = spec.sine_amplitude
                phi_rad = np.radians(spec.sine_phase)
                phasor_val = amp * np.exp(1j * (phi_rad - np.pi / 2))
                func = lambda t, pv=phasor_val: pv
            elif spec:
                time_func = spec.time_function()
                func = lambda t, f=time_func, p=phasor: f(t) * np.exp(-1j * p.theta(t))
            else:
                func = lambda t: 0.0 + 0.0j
            isrc_phasor_info.append((np_idx, nm_idx, func))

        n_total = mna.n_total
        vsrc_offset = mna.n_nodes

        def b_phasor_func(t: float) -> np.ndarray:
            b = np.zeros(n_total, dtype=complex)
            # Current sources
            for np_idx, nm_idx, func in isrc_phasor_info:
                val = func(t)
                if np_idx >= 0:
                    b[np_idx] -= val
                if nm_idx >= 0:
                    b[nm_idx] += val
            # Voltage sources: same sign as time-domain b_func.
            # KVL row: 0 = +V(n+) - V(n-) + b[branch] -> b[branch] = -vs_phasor(t)
            for k, func in enumerate(vsrc_phasor_funcs):
                b[vsrc_offset + k] = -func(t)
            return b

        return b_phasor_func

    def phasor_domain_ode(self, t: float, x_d_ri: np.ndarray,
                          b_phasor_func: Callable) -> np.ndarray:
        """
        Phasor-domain ODE (reduced system).

        The phasor transform modifies the MNA:
            E * (dX_tilde/dt + j*omega*X_tilde) = A*X_tilde + b_tilde(t)
        ->  E * dX_tilde/dt = (A - j*omega*E)*X_tilde + b_tilde(t)

        With algebraic elimination, the effective system matrix
        becomes (A - j*omega*E) instead of A, but only E_dd has nonzero
        entries so the j*omega term only affects the differential block.

        State is interleaved: [Re(x_d[0]), Im(x_d[0]), Re(x_d[1]), ...]
        """
        if not self._use_reduced:
            n = self.mna.n_total
            x_complex = x_d_ri[0::2] + 1j * x_d_ri[1::2]
            b = b_phasor_func(t)
            dx_complex = self._M_dp_full @ x_complex + self._E_inv @ b
            result = np.zeros(2 * n)
            result[0::2] = np.real(dx_complex)
            result[1::2] = np.imag(dx_complex)
            return result

        # Reduced system: dx_d/dt = M_dp @ x_d + reduced excitation g(t)
        nd = self._n_diff
        x_d = x_d_ri[0::2] + 1j * x_d_ri[1::2]  # (n_diff,)

        b_full = b_phasor_func(t)
        dx_d = self._M_dp @ x_d + self._reduced_g(b_full)

        result = np.zeros(2 * nd)
        result[0::2] = np.real(dx_d)
        result[1::2] = np.imag(dx_d)
        return result

    def solve_phasor_domain(self, t_span: Tuple[float, float] = None,
                            t_eval: np.ndarray = None,
                            x0: np.ndarray = None,
                            **solver_kwargs) -> Dict:
        """
        Solve the circuit in the phasor domain.

        Parameters
        ----------
        t_span, t_eval, x0 : same as solve_time_domain
        **solver_kwargs : passed to solve_ivp

        Returns
        -------
        dict
            Same keys as time-domain results, plus:
            'phasor_voltages' : dict {node: complex phasor array}
            'phasor_currents' : dict {branch: complex phasor array}
            'envelopes' : dict {label: envelope (magnitude) array}
        """
        if self.phasor is None:
            self.configure_phasor()

        _n = self.mna.n_total

        # Time span
        if t_span is None:
            tran = self.netlist.tran_params()
            if tran:
                t_start = tran.get('t_start', 0.0)
                t_stop = tran['t_stop']
                t_span = (t_start, t_stop)
            else:
                raise ValueError("No .tran command; provide t_span")

        # Initial conditions (complex, stored as interleaved real/imag)
        if self._use_reduced:
            n_state = self._n_diff
        else:
            n_state = self.mna.n_total

        # Build phasor excitation
        b_phasor_func = self._build_phasor_b_func()

        if x0 is None:
            x0_ri = np.zeros(2 * n_state)
        else:
            if np.iscomplexobj(x0):
                # x0 is full complex state - extract differential part
                if self._use_reduced:
                    x0_d = self._reduced_ic(x0)
                else:
                    x0_d = x0
                x0_ri = np.zeros(2 * n_state)
                x0_ri[0::2] = np.real(x0_d)
                x0_ri[1::2] = np.imag(x0_d)
            else:
                x0_ri = np.zeros(2 * n_state)
                x0_ri[0::2] = x0[:n_state] if len(x0) >= n_state else x0

        # Time eval
        if t_eval is None:
            n_points = max(2000, min(10000, int(50000 * (t_span[1] - t_span[0]))))
            t_eval = np.linspace(t_span[0], t_span[1], n_points)

        # Build analytical Jacobian (constant for fixed omega)
        # The ODE is dx_ri/dt = f(x_ri) where the complex Jacobian is M_dp.
        # For the real-valued interleaved state [Re(x0), Im(x0), Re(x1), ...]:
        #   J_real = block_diag of 2x2 blocks [[Re(M_dp_ij), -Im(M_dp_ij)],
        #                                       [Im(M_dp_ij),  Re(M_dp_ij)]]
        if self._use_reduced:
            M_dp = self._M_dp
        else:
            M_dp = self._M_dp_full
        nd = M_dp.shape[0]
        J_real = np.zeros((2 * nd, 2 * nd))
        J_real[0::2, 0::2] = np.real(M_dp)
        J_real[0::2, 1::2] = -np.imag(M_dp)
        J_real[1::2, 0::2] = np.imag(M_dp)
        J_real[1::2, 1::2] = np.real(M_dp)

        # Solve - use implicit method if stiff
        default_method = 'Radau' if self._is_stiff else 'RK45'
        sol = solve_ivp(
            lambda t, y: self.phasor_domain_ode(t, y, b_phasor_func),
            t_span, x0_ri, t_eval=t_eval,
            method=solver_kwargs.pop('method', default_method),
            rtol=solver_kwargs.pop('rtol', 1e-8),
            atol=solver_kwargs.pop('atol', 1e-10),
            jac=J_real,
            **solver_kwargs,
        )

        if not sol.success:
            warnings.warn(f"Phasor ODE solver warning: {sol.message}")

        # Reconstruct full complex phasor state at each time point
        n_total = self.mna.n_total
        x_phasor_full = np.zeros((n_total, len(sol.t)), dtype=complex)

        for j in range(len(sol.t)):
            x_d = sol.y[0::2, j] + 1j * sol.y[1::2, j]
            if self._use_reduced:
                # Recover the full complex phasor state (handles both the block
                # reduction and the general coordinate-transform reduction).
                b_full = b_phasor_func(sol.t[j])
                x_phasor_full[:, j] = self._recover_full(x_d, b_full)
            else:
                x_phasor_full[:, j] = x_d

        results = self._package_phasor_results(sol.t, x_phasor_full)
        results["solver_stats"] = {
            "nfev": int(sol.nfev),
            "njev": int(getattr(sol, "njev", 0)),
            "nlu": int(getattr(sol, "nlu", 0)),
        }
        return results

    def _package_phasor_results(self, t: np.ndarray, x_phasor: np.ndarray) -> Dict:
        """
        Package phasor ODE results.

        Parameters
        ----------
        t : ndarray (n_points,)
        x_phasor : ndarray (n_total, n_points) - complex
        """
        mna = self.mna

        results = {'t': t}

        # Phasor quantities
        phasor_voltages = {}
        phasor_currents = {}
        envelopes = {}

        for i in range(mna.n_nodes):
            name = mna.node_map.name(i)
            phasor_v = x_phasor[i]
            phasor_voltages[name] = phasor_v

            # Reconstruct time-domain
            real_v = self.phasor.to_real(phasor_v, t)
            results[f"V({name})"] = real_v
            envelopes[f"V({name})"] = np.abs(phasor_v)

        for k, bname in enumerate(mna.vsrc_names):
            phasor_i = x_phasor[mna.n_nodes + k]
            phasor_currents[bname] = phasor_i
            results[f"I({bname})"] = self.phasor.to_real(phasor_i, t)
            envelopes[f"I({bname})"] = np.abs(phasor_i)

        for k, bname in enumerate(mna.ind_names):
            phasor_i = x_phasor[mna.n_nodes + mna.n_vsrc + k]
            phasor_currents[bname] = phasor_i
            results[f"I({bname})"] = self.phasor.to_real(phasor_i, t)
            envelopes[f"I({bname})"] = np.abs(phasor_i)

        results['phasor_voltages'] = phasor_voltages
        results['phasor_currents'] = phasor_currents
        results['envelopes'] = envelopes
        results['x_phasor'] = x_phasor

        return results

    # ----------------------------------------------------------
    # Fixed-step complex trapezoidal DP solver
    # ----------------------------------------------------------

    def solve_phasor_domain_fixed(self, t_span: Tuple[float, float] = None,
                                   n_steps: int = None,
                                   x0: np.ndarray = None,
                                   steps_per_period: int = 200,
                                   init_steady_state: bool = False,
                                   use_sparse: str = 'auto',
                                   use_cpp=None) -> Dict:
        """
        Solve the phasor-domain ODE with a fixed-step complex trapezoidal method.

        Pre-factorises the LHS matrix once, giving O(n^2) per step instead of
        the O(n^3) per Radau step.  Best for benchmarking at fixed frequency.

        Complex trapezoidal rule for dx/dt = M_dp*x + g(t):
            (I - dt/2 * M_dp) * x_{n+1} = (I + dt/2 * M_dp) * x_n
                                            + dt/2 * (g_n + g_{n+1})

        Parameters
        ----------
        t_span : tuple, optional
        n_steps : int, optional
            Number of time steps. Overrides steps_per_period if given.
        x0 : ndarray, optional
        steps_per_period : int, optional
            Steps per carrier period for auto step-count. Default 200.
        init_steady_state : bool, optional
            If True and source is constant, initialize with the analytical
            steady-state solution x_ss = -M_dp^{-1}*g instead of zero.
            Essential for multi-source networks with slow transient modes.

        Returns
        -------
        dict  – same format as solve_phasor_domain
        """
        self._assert_not_general("solve_phasor_domain_fixed")
        if self.phasor is None:
            self.configure_phasor()

        # Time span
        if t_span is None:
            tran = self.netlist.tran_params()
            if tran:
                t_span = (tran.get('t_start', 0.0), tran['t_stop'])
            else:
                raise ValueError("No .tran command; provide t_span")

        # Determine step count
        if n_steps is None:
            period = 2 * np.pi / self.omega_s
            n_periods = (t_span[1] - t_span[0]) / period
            n_steps = max(200, int(steps_per_period * n_periods))

        dt = (t_span[1] - t_span[0]) / n_steps
        t_arr = np.linspace(t_span[0], t_span[1], n_steps + 1)

        # Build phasor excitation
        b_phasor_func = self._build_phasor_b_func()

        # Branch: nonlinear vs linear path
        if self._nonlinear_elements:
            # Use full MNA system with complex NR
            n_total = self.mna.n_total
            n_nodes = self.mna.n_nodes
            if x0 is None:
                x0_full = np.zeros(n_total, dtype=complex)
                if init_steady_state:
                    # Compute steady-state including nonlinear elements.
                    # Solve A_dp*x + b + f_nl(x) = 0 via NR iterations
                    # starting from the linear steady state.
                    A_dp = self.mna.A - 1j * self.omega_s * self.mna.E
                    b0 = b_phasor_func(0.0)
                    try:
                        x0_full = np.linalg.solve(-A_dp, b0)
                    except np.linalg.LinAlgError:
                        import warnings
                        warnings.warn("Singular A_dp in steady-state init; using zero initial guess")
                    # NR iterations to include nonlinear elements
                    for _ in range(20):
                        v_k = x0_full[:n_nodes]
                        i_nl, J_nl = self._eval_nonlinear_phasor(v_k, 0.0)
                        f_nl = np.zeros(n_total, dtype=complex)
                        f_nl[:n_nodes] = i_nl
                        residual = A_dp @ x0_full + b0 + f_nl
                        Jac = A_dp.copy().astype(complex)
                        Jac[:n_nodes, :n_nodes] += J_nl
                        try:
                            dx = np.linalg.solve(Jac, -residual)
                        except np.linalg.LinAlgError as e:
                            import warnings
                            warnings.warn(f"Nonlinear solver initialization failed: {e}. Using flat start.")
                            break
                        x0_full += dx
                        if np.max(np.abs(dx)) < 1e-10:
                            break
            elif np.iscomplexobj(x0):
                x0_full = x0.copy()
            else:
                x0_full = x0.astype(complex)
            return self._solve_phasor_fixed_nonlinear(
                t_arr, dt, n_steps, x0_full, b_phasor_func)

        # Determine sparse vs dense
        n_eff = self._n_diff if self._use_reduced else self.mna.n_total
        if use_sparse == 'auto':
            _sparse = self._should_use_sparse(n_eff)
        else:
            _sparse = bool(use_sparse)

        # C++ acceleration
        _use_cpp = use_cpp if use_cpp is not None else (HAS_CPP and not _sparse)
        _use_cpp = _use_cpp and HAS_CPP
        if _use_cpp and self._use_reduced and self._n_diff == 0:
            _use_cpp = False

        if _use_cpp:
            x_phasor_full = self._solve_dp_cpp(
                t_arr, dt, n_steps, x0, b_phasor_func, init_steady_state)
        elif self._use_reduced:
            nd = self._n_diff
            M_dp = self._M_dp

            # Initial condition
            if x0 is None:
                x_d = np.zeros(nd, dtype=complex)
            elif np.iscomplexobj(x0):
                x_d = x0[self._diff_idx] if len(x0) > nd else x0
            else:
                x_d = x0[:nd].astype(complex)

            # Storage for full phasor state
            n_total = self.mna.n_total
            x_phasor_full = np.zeros((n_total, n_steps + 1), dtype=complex)

            diff_idx = self._diff_idx
            alg_idx = self._alg_idx

            # Step 4: Constant-source fast path (SINE sources at carrier)
            b0 = b_phasor_func(0.0)
            b1 = b_phasor_func(1.0)
            is_constant_source = np.allclose(b0, b1)

            if _sparse:
                import scipy.sparse as sp
                import scipy.sparse.linalg as spla
                M_dp_sp = sp.csc_matrix(M_dp)
                I_sp = sp.eye(nd, format='csc', dtype=complex)
                LHS_sp = I_sp - (dt / 2) * M_dp_sp
                RHS_sp = I_sp + (dt / 2) * M_dp_sp
                lu_obj = spla.splu(LHS_sp)
                E_dd_inv_sp = sp.csc_matrix(self._E_dd_inv)
                E_dd_inv_A_da_Aaa_inv_sp = sp.csc_matrix(self._E_dd_inv_A_da_Aaa_inv)
                A_aa_inv_sp = sp.csc_matrix(self._A_aa_inv_cached)
                A_ad_sp = sp.csc_matrix(self._A_ad)

                def g_func_with_ba_sp(t_val):
                    b_full = b_phasor_func(t_val)
                    b_d = b_full[diff_idx]
                    b_a = b_full[alg_idx]
                    g = E_dd_inv_sp.dot(b_d) - E_dd_inv_A_da_Aaa_inv_sp.dot(b_a)
                    return g, b_a

                if is_constant_source:
                    g_const, b_a_const = g_func_with_ba_sp(0.0)
                    dt_g_const = dt * g_const

                    if x0 is None and init_steady_state:
                        x_d = np.linalg.solve(-M_dp, g_const)

                    x_a = -A_aa_inv_sp.dot(A_ad_sp.dot(x_d) + b_a_const)
                    x_phasor_full[diff_idx, 0] = x_d
                    x_phasor_full[alg_idx, 0] = x_a

                    for i in range(n_steps):
                        rhs = RHS_sp.dot(x_d) + dt_g_const
                        x_d = lu_obj.solve(rhs)
                        x_a = -A_aa_inv_sp.dot(A_ad_sp.dot(x_d) + b_a_const)
                        x_phasor_full[diff_idx, i + 1] = x_d
                        x_phasor_full[alg_idx, i + 1] = x_a
                else:
                    g_n, b_a_n = g_func_with_ba_sp(t_arr[0])
                    x_a = -A_aa_inv_sp.dot(A_ad_sp.dot(x_d) + b_a_n)
                    x_phasor_full[diff_idx, 0] = x_d
                    x_phasor_full[alg_idx, 0] = x_a

                    for i in range(n_steps):
                        g_n1, b_a_n1 = g_func_with_ba_sp(t_arr[i + 1])
                        rhs = RHS_sp.dot(x_d) + (dt / 2) * (g_n + g_n1)
                        x_d = lu_obj.solve(rhs)
                        g_n = g_n1
                        x_a = -A_aa_inv_sp.dot(A_ad_sp.dot(x_d) + b_a_n1)
                        x_phasor_full[diff_idx, i + 1] = x_d
                        x_phasor_full[alg_idx, i + 1] = x_a
            else:
                from scipy.linalg import lu_factor, lu_solve
                I_nd = np.eye(nd, dtype=complex)
                LHS = I_nd - (dt / 2) * M_dp
                RHS_mat = I_nd + (dt / 2) * M_dp
                lu, piv = lu_factor(LHS)

                E_dd_inv = self._E_dd_inv
                _A_da_Aaa_inv = self._A_da_Aaa_inv
                E_dd_inv_A_da_Aaa_inv = self._E_dd_inv_A_da_Aaa_inv
                A_aa_inv_cached = self._A_aa_inv_cached
                A_ad = self._A_ad

                def g_func_with_ba(t_val):
                    b_full = b_phasor_func(t_val)
                    b_d = b_full[diff_idx]
                    b_a = b_full[alg_idx]
                    g = E_dd_inv @ b_d - E_dd_inv_A_da_Aaa_inv @ b_a
                    return g, b_a

                if is_constant_source:
                    g_const, b_a_const = g_func_with_ba(0.0)
                    dt_g_const = dt * g_const

                    if x0 is None and init_steady_state:
                        x_d = np.linalg.solve(-M_dp, g_const)

                    x_a = -A_aa_inv_cached @ (A_ad @ x_d + b_a_const)
                    x_phasor_full[diff_idx, 0] = x_d
                    x_phasor_full[alg_idx, 0] = x_a

                    for i in range(n_steps):
                        rhs = RHS_mat @ x_d + dt_g_const
                        x_d = lu_solve((lu, piv), rhs)

                        x_a = -A_aa_inv_cached @ (A_ad @ x_d + b_a_const)
                        x_phasor_full[diff_idx, i + 1] = x_d
                        x_phasor_full[alg_idx, i + 1] = x_a
                else:
                    g_n, b_a_n = g_func_with_ba(t_arr[0])

                    x_a = -A_aa_inv_cached @ (A_ad @ x_d + b_a_n)
                    x_phasor_full[diff_idx, 0] = x_d
                    x_phasor_full[alg_idx, 0] = x_a

                    for i in range(n_steps):
                        g_n1, b_a_n1 = g_func_with_ba(t_arr[i + 1])
                        rhs = RHS_mat @ x_d + (dt / 2) * (g_n + g_n1)
                        x_d = lu_solve((lu, piv), rhs)
                        g_n = g_n1

                        x_a = -A_aa_inv_cached @ (A_ad @ x_d + b_a_n1)
                        x_phasor_full[diff_idx, i + 1] = x_d
                        x_phasor_full[alg_idx, i + 1] = x_a

        else:
            # Non-reduced (pure ODE) case
            n = self.mna.n_total
            M_dp = self._M_dp_full

            if _sparse:
                import scipy.sparse as sp
                import scipy.sparse.linalg as spla
                M_dp_sp = sp.csc_matrix(M_dp)
                I_sp = sp.eye(n, format='csc', dtype=complex)
                LHS_sp = I_sp - (dt / 2) * M_dp_sp
                RHS_sp = I_sp + (dt / 2) * M_dp_sp
                lu_obj = spla.splu(LHS_sp)

                if x0 is None:
                    x_c = np.zeros(n, dtype=complex)
                elif np.iscomplexobj(x0):
                    x_c = x0.copy()
                else:
                    x_c = x0.astype(complex)

                n_total = n
                x_phasor_full = np.zeros((n_total, n_steps + 1), dtype=complex)
                x_phasor_full[:, 0] = x_c

                E_inv_sp = sp.csc_matrix(self._E_inv)
                g_n = E_inv_sp.dot(b_phasor_func(t_arr[0]))

                for i in range(n_steps):
                    g_n1 = E_inv_sp.dot(b_phasor_func(t_arr[i + 1]))
                    rhs = RHS_sp.dot(x_c) + (dt / 2) * (g_n + g_n1)
                    x_c = lu_obj.solve(rhs)
                    g_n = g_n1
                    x_phasor_full[:, i + 1] = x_c
            else:
                from scipy.linalg import lu_factor, lu_solve
                I_n = np.eye(n, dtype=complex)
                LHS = I_n - (dt / 2) * M_dp
                RHS_mat = I_n + (dt / 2) * M_dp
                lu, piv = lu_factor(LHS)

                if x0 is None:
                    x_c = np.zeros(n, dtype=complex)
                elif np.iscomplexobj(x0):
                    x_c = x0.copy()
                else:
                    x_c = x0.astype(complex)

                n_total = n
                x_phasor_full = np.zeros((n_total, n_steps + 1), dtype=complex)
                x_phasor_full[:, 0] = x_c

                g_n = self._E_inv @ b_phasor_func(t_arr[0])

                for i in range(n_steps):
                    g_n1 = self._E_inv @ b_phasor_func(t_arr[i + 1])
                    rhs = RHS_mat @ x_c + (dt / 2) * (g_n + g_n1)
                    x_c = lu_solve((lu, piv), rhs)
                    g_n = g_n1
                    x_phasor_full[:, i + 1] = x_c

        results = self._package_phasor_results(t_arr, x_phasor_full)
        results["solver_stats"] = {
            "nfev": n_steps,
            "njev": 0,
            "nlu": 1,
            "n_steps": n_steps,
            "dt": dt,
            "backend": "cpp" if _use_cpp else "python",
        }
        return results

    def _solve_dp_cpp(self, t_arr, dt, n_steps, x0, b_phasor_func,
                      init_steady_state):
        """DP phasor solve via C++ backend — uses scipy LU for identical results."""
        from scipy.linalg import lu_factor

        if self._use_reduced:
            nd = self._n_diff
            M_dp = self._M_dp
            diff_idx = self._diff_idx
            alg_idx = self._alg_idx
            E_dd_inv = self._E_dd_inv
            E_dd_inv_A_da_Aaa_inv = self._E_dd_inv_A_da_Aaa_inv
            A_aa_inv_cached = self._A_aa_inv_cached
            A_ad = self._A_ad

            b0 = b_phasor_func(0.0)
            b1 = b_phasor_func(1.0)
            is_constant = np.allclose(b0, b1)

            if x0 is None:
                x_d = np.zeros(nd, dtype=complex)
            elif np.iscomplexobj(x0):
                x_d = x0[diff_idx] if len(x0) > nd else x0.copy()
            else:
                x_d = x0[:nd].astype(complex)

            # Build LHS/RHS (same as Python path)
            I_nd = np.eye(nd, dtype=complex)
            LHS = I_nd - (dt / 2) * M_dp
            RHS_mat = np.ascontiguousarray(I_nd + (dt / 2) * M_dp)
            lu_data, piv = lu_factor(LHS)

            if is_constant:
                b_d = b0[diff_idx]
                b_a = b0[alg_idx]
                g_const = (E_dd_inv @ b_d
                           - E_dd_inv_A_da_Aaa_inv @ b_a).astype(complex)

                if x0 is None and init_steady_state:
                    x_d = np.linalg.solve(-M_dp, g_const)

                X_d = dpspice_cpp.dp_phasor_solve_constant(
                    RHS_mat, np.asfortranarray(lu_data),
                    piv.astype(np.int32),
                    np.ascontiguousarray(g_const),
                    np.ascontiguousarray(x_d), dt, n_steps)
            else:
                g_all = np.empty((n_steps + 1, nd), dtype=complex)
                n_alg = len(alg_idx)
                b_a_all = np.empty((n_steps + 1, n_alg), dtype=complex)
                for i in range(n_steps + 1):
                    b_full = b_phasor_func(t_arr[i])
                    b_d = b_full[diff_idx]
                    b_a = b_full[alg_idx]
                    g_all[i] = E_dd_inv @ b_d - E_dd_inv_A_da_Aaa_inv @ b_a
                    b_a_all[i] = b_a

                if x0 is None and init_steady_state:
                    x_d = np.linalg.solve(-M_dp, g_all[0])

                X_d = dpspice_cpp.dp_phasor_solve(
                    RHS_mat, np.asfortranarray(lu_data),
                    piv.astype(np.int32), g_all,
                    np.ascontiguousarray(x_d, dtype=complex), dt)

            # Reconstruct algebraic vars
            n_total = self.mna.n_total
            x_phasor_full = np.zeros((n_total, n_steps + 1), dtype=complex)
            x_phasor_full[diff_idx, :] = X_d.T

            if is_constant:
                b_a_const = b0[alg_idx]
                x_phasor_full[alg_idx, :] = -(
                    A_aa_inv_cached @ (A_ad @ X_d.T + b_a_const[:, None]))
            else:
                x_phasor_full[alg_idx, :] = -(
                    A_aa_inv_cached @ (A_ad @ X_d.T + b_a_all.T))

        else:
            n = self.mna.n_total
            M_dp = self._M_dp_full
            E_inv = self._E_inv

            if x0 is None:
                x_c = np.zeros(n, dtype=complex)
            elif np.iscomplexobj(x0):
                x_c = x0.copy()
            else:
                x_c = x0.astype(complex)

            b0 = b_phasor_func(0.0)
            b1 = b_phasor_func(1.0)
            is_constant = np.allclose(b0, b1)

            I_n = np.eye(n, dtype=complex)
            LHS = I_n - (dt / 2) * M_dp
            RHS_mat = np.ascontiguousarray(I_n + (dt / 2) * M_dp)
            lu_data, piv = lu_factor(LHS)

            if is_constant:
                g_const = np.ascontiguousarray(E_inv @ b0, dtype=complex)
                if x0 is None and init_steady_state:
                    x_c = np.linalg.solve(-M_dp, g_const)
                X = dpspice_cpp.dp_phasor_solve_constant(
                    RHS_mat, np.asfortranarray(lu_data),
                    piv.astype(np.int32), g_const,
                    np.ascontiguousarray(x_c, dtype=complex), dt, n_steps)
            else:
                g_all = np.empty((n_steps + 1, n), dtype=complex)
                for i in range(n_steps + 1):
                    g_all[i] = E_inv @ b_phasor_func(t_arr[i])
                if x0 is None and init_steady_state:
                    x_c = np.linalg.solve(-M_dp, g_all[0])
                X = dpspice_cpp.dp_phasor_solve(
                    RHS_mat, np.asfortranarray(lu_data),
                    piv.astype(np.int32), g_all,
                    np.ascontiguousarray(x_c, dtype=complex), dt)

            x_phasor_full = X.T  # (n, n_steps+1)

        return x_phasor_full

    # ----------------------------------------------------------
    # Multi-phasor harmonic solver
    # ----------------------------------------------------------

    def _build_harmonic_b_func(self, k: int) -> Callable[[float], np.ndarray]:
        """
        Build phasor-domain excitation vector for the k-th harmonic.

        For a SINE source at frequency omega_src:
          - k=1 (fundamental): constant phasor if omega_src == omega_s
          - k=0 (DC): rotating phasor from DC offsets
          - k!=1: zero for pure SINE at fundamental (nonzero for PULSE/PWL)

        For PULSE/PWM sources: rectangular Fourier series gives
          b_k = (2/(k*pi)) * (V2-V1) * sin(k*pi*D) * e^(-j*k*phase)
        where D = duty cycle.
        """
        mna = self.mna
        omega_s = self.omega_s

        # Pre-compute phasor source functions for harmonic k
        vsrc_phasor_funcs = []
        for elem in self.netlist.voltage_sources():
            spec = elem.source_spec
            if spec is None:
                vsrc_phasor_funcs.append(lambda t: 0.0 + 0.0j)
                continue

            from netlist_parser import SourceType

            if spec.source_type == SourceType.SINE:
                amp = spec.sine_amplitude
                phi_rad = np.radians(spec.sine_phase)
                offset = spec.sine_offset
                omega_src = 2 * np.pi * spec.sine_freq

                if k == 1 and abs(omega_src - omega_s) < 1.0:
                    # Fundamental: same as single-phasor case
                    phasor_val = amp * np.exp(1j * (phi_rad - np.pi / 2))
                    vsrc_phasor_funcs.append(lambda t, pv=phasor_val: pv)
                elif k == 0 and abs(offset) > 1e-15:
                    # DC component from offset
                    vsrc_phasor_funcs.append(lambda t, dc=offset: complex(dc, 0))
                else:
                    # Higher harmonics of a pure SINE: zero
                    vsrc_phasor_funcs.append(lambda t: 0.0 + 0.0j)

            elif spec.source_type == SourceType.PULSE:
                # Rectangular Fourier series for PULSE
                # v(t) = V1 + (V2-V1) * rect(t/DT), Fourier decomposition:
                #   V_0 = V1 + (V2-V1)*D   (DC)
                #   V_k = 2*(V2-V1)*sin(k*pi*D)/(k*pi) * e^{-jk*pi*D}  (k>=1)
                # with additional phase shift e^{-jk*omega*td} for pulse delay
                v1, v2 = spec.pulse_v1, spec.pulse_v2
                period = spec.pulse_period
                if period > 0:
                    duty = spec.pulse_on / period
                    if k == 0:
                        # DC component: V1 + (V2-V1)*D
                        dc = v1 + (v2 - v1) * duty
                        vsrc_phasor_funcs.append(lambda t, d=dc: complex(d, 0))
                    else:
                        # k-th harmonic coefficient (complex Fourier series)
                        coeff = 2 * (v2 - v1) * np.sin(k * np.pi * duty) / (k * np.pi)
                        # Phase: Fourier phase + pulse delay
                        td = spec.pulse_delay
                        phase = -k * np.pi * duty - k * omega_s * td
                        pv = coeff * np.exp(1j * phase)
                        vsrc_phasor_funcs.append(lambda t, p=pv: p)
                else:
                    vsrc_phasor_funcs.append(lambda t: 0.0 + 0.0j)

            elif spec.source_type == SourceType.DC:
                if k == 0:
                    dc = spec.dc_value
                    vsrc_phasor_funcs.append(lambda t, d=dc: complex(d, 0))
                else:
                    vsrc_phasor_funcs.append(lambda t: 0.0 + 0.0j)
            else:
                vsrc_phasor_funcs.append(lambda t: 0.0 + 0.0j)

        # Current sources (similar logic)
        isrc_list = self.netlist.current_sources()
        isrc_phasor_info = []
        for elem in isrc_list:
            np_idx = mna.node_map.idx(elem.nodes[0])
            nm_idx = mna.node_map.idx(elem.nodes[1])
            isrc_phasor_info.append((np_idx, nm_idx, lambda t: 0.0 + 0.0j))

        n_total = mna.n_total
        vsrc_offset = mna.n_nodes

        def b_harmonic_func(t: float) -> np.ndarray:
            b = np.zeros(n_total, dtype=complex)
            for np_idx, nm_idx, func in isrc_phasor_info:
                val = func(t)
                if np_idx >= 0:
                    b[np_idx] -= val
                if nm_idx >= 0:
                    b[nm_idx] += val
            for idx, func in enumerate(vsrc_phasor_funcs):
                b[vsrc_offset + idx] = -func(t)
            return b

        return b_harmonic_func

    def _solve_single_harmonic(self, k: int, t_span: Tuple[float, float],
                                t_eval: np.ndarray, **solver_kwargs) -> Dict:
        """
        Solve the k-th harmonic phasor ODE independently.

        The ODE for harmonic k is:
            E * dX_k/dt = (A - j*k*omega*E) * X_k + b_k(t)

        Same structure as single-phasor but with frequency shift j*k*omega.
        """
        omega_s = self.omega_s

        b_k_func = self._build_harmonic_b_func(k)

        # Pre-compute M_dp_k = M_reduced - j*k*omega*I for this harmonic
        if self._use_reduced:
            n_state = self._n_diff
            M_dp_k = self._M_reduced - 1j * k * omega_s * np.eye(n_state)

            def ode_func(t, x_ri):
                x_d = x_ri[0::2] + 1j * x_ri[1::2]
                b_full = b_k_func(t)
                b_d = b_full[self._diff_idx]
                b_a = b_full[self._alg_idx]
                b_reduced = b_d - self._A_da_Aaa_inv @ b_a
                dx_d = M_dp_k @ x_d + self._E_dd_inv @ b_reduced
                result = np.zeros(2 * n_state)
                result[0::2] = np.real(dx_d)
                result[1::2] = np.imag(dx_d)
                return result
        else:
            n_state = self.mna.n_total
            M_dp_k = self._E_inv @ (self.mna.A - 1j * k * omega_s * self.mna.E)

            def ode_func(t, x_ri):
                x = x_ri[0::2] + 1j * x_ri[1::2]
                b = b_k_func(t)
                dx = M_dp_k @ x + self._E_inv @ b
                result = np.zeros(2 * n_state)
                result[0::2] = np.real(dx)
                result[1::2] = np.imag(dx)
                return result

        # Build analytical Jacobian for harmonic k
        nd = M_dp_k.shape[0]
        J_real = np.zeros((2 * nd, 2 * nd))
        J_real[0::2, 0::2] = np.real(M_dp_k)
        J_real[0::2, 1::2] = -np.imag(M_dp_k)
        J_real[1::2, 0::2] = np.imag(M_dp_k)
        J_real[1::2, 1::2] = np.real(M_dp_k)

        x0_ri = np.zeros(2 * n_state)

        default_method = 'Radau' if self._is_stiff else 'RK45'
        sol = solve_ivp(
            ode_func, t_span, x0_ri, t_eval=t_eval,
            method=solver_kwargs.pop('method', default_method),
            rtol=solver_kwargs.pop('rtol', 1e-8),
            atol=solver_kwargs.pop('atol', 1e-10),
            jac=J_real,
            **solver_kwargs,
        )

        if not sol.success:
            warnings.warn(f"Harmonic k={k} solver warning: {sol.message}")

        # Reconstruct full complex state
        n_total = self.mna.n_total
        x_phasor = np.zeros((n_total, len(sol.t)), dtype=complex)
        for j in range(len(sol.t)):
            x_d = sol.y[0::2, j] + 1j * sol.y[1::2, j]
            if self._use_reduced:
                b_full = b_k_func(sol.t[j])
                b_a = b_full[self._alg_idx]
                x_a = -self._A_aa_inv_cached @ (self._A_ad @ x_d + b_a)
                x_phasor[self._diff_idx, j] = x_d
                x_phasor[self._alg_idx, j] = x_a
            else:
                x_phasor[:, j] = x_d

        return {
            't': sol.t,
            'x_phasor': x_phasor,
            'nfev': int(sol.nfev),
        }

    def _solve_single_harmonic_fixed(self, k: int, t_arr: np.ndarray,
                                      dt: float,
                                      init_steady_state: bool = False) -> Dict:
        """
        Solve the k-th harmonic phasor ODE with fixed-step complex trapezoidal.

        Same structure as solve_phasor_domain_fixed but with frequency shift
        j*k*omega instead of j*omega.
        """
        from scipy.linalg import lu_factor, lu_solve

        omega_s = self.omega_s
        b_k_func = self._build_harmonic_b_func(k)
        n_steps = len(t_arr) - 1

        if self._use_reduced:
            nd = self._n_diff
            M_dp_k = self._M_reduced - 1j * k * omega_s * np.eye(nd)

            I_nd = np.eye(nd, dtype=complex)
            LHS = I_nd - (dt / 2) * M_dp_k
            RHS_mat = I_nd + (dt / 2) * M_dp_k
            lu, piv = lu_factor(LHS)

            x_d = np.zeros(nd, dtype=complex)

            # Steady-state initialisation: x_ss = -M_dp_k^{-1} g
            if init_steady_state:
                b0 = b_k_func(0.0)
                diff_idx = self._diff_idx
                alg_idx = self._alg_idx
                b_d = b0[diff_idx]
                b_a = b0[alg_idx]
                g0 = self._E_dd_inv @ b_d - self._E_dd_inv_A_da_Aaa_inv @ b_a
                x_d = np.linalg.solve(-M_dp_k, g0)
            n_total = self.mna.n_total
            x_phasor_full = np.zeros((n_total, n_steps + 1), dtype=complex)

            # Local references for hot-loop efficiency
            diff_idx = self._diff_idx
            alg_idx = self._alg_idx
            E_dd_inv = self._E_dd_inv
            E_dd_inv_A_da_Aaa_inv = self._E_dd_inv_A_da_Aaa_inv
            A_aa_inv_cached = self._A_aa_inv_cached
            A_ad = self._A_ad

            def g_func_with_ba(t_val):
                b_full = b_k_func(t_val)
                b_d = b_full[diff_idx]
                b_a = b_full[alg_idx]
                g = E_dd_inv @ b_d - E_dd_inv_A_da_Aaa_inv @ b_a
                return g, b_a

            # Constant-source fast path
            b0 = b_k_func(0.0)
            b1 = b_k_func(1.0)
            is_constant_source = np.allclose(b0, b1)

            if is_constant_source:
                g_const, b_a_const = g_func_with_ba(0.0)
                dt_g_const = dt * g_const

                x_a = -A_aa_inv_cached @ (A_ad @ x_d + b_a_const)
                x_phasor_full[diff_idx, 0] = x_d
                x_phasor_full[alg_idx, 0] = x_a

                for i in range(n_steps):
                    rhs = RHS_mat @ x_d + dt_g_const
                    x_d = lu_solve((lu, piv), rhs)

                    x_a = -A_aa_inv_cached @ (A_ad @ x_d + b_a_const)
                    x_phasor_full[diff_idx, i + 1] = x_d
                    x_phasor_full[alg_idx, i + 1] = x_a
            else:
                g_n, b_a_n = g_func_with_ba(t_arr[0])

                x_a = -A_aa_inv_cached @ (A_ad @ x_d + b_a_n)
                x_phasor_full[diff_idx, 0] = x_d
                x_phasor_full[alg_idx, 0] = x_a

                for i in range(n_steps):
                    g_n1, b_a_n1 = g_func_with_ba(t_arr[i + 1])
                    rhs = RHS_mat @ x_d + (dt / 2) * (g_n + g_n1)
                    x_d = lu_solve((lu, piv), rhs)
                    g_n = g_n1

                    x_a = -A_aa_inv_cached @ (A_ad @ x_d + b_a_n1)
                    x_phasor_full[diff_idx, i + 1] = x_d
                    x_phasor_full[alg_idx, i + 1] = x_a
        else:
            n = self.mna.n_total
            M_dp_k = self._E_inv @ (self.mna.A - 1j * k * omega_s * self.mna.E)

            I_n = np.eye(n, dtype=complex)
            LHS = I_n - (dt / 2) * M_dp_k
            RHS_mat = I_n + (dt / 2) * M_dp_k
            lu, piv = lu_factor(LHS)

            x_c = np.zeros(n, dtype=complex)

            # Steady-state initialisation for non-reduced system
            if init_steady_state:
                g0 = self._E_inv @ b_k_func(0.0)
                x_c = np.linalg.solve(-M_dp_k, g0)

            n_total = n
            x_phasor_full = np.zeros((n_total, n_steps + 1), dtype=complex)
            x_phasor_full[:, 0] = x_c

            g_n = self._E_inv @ b_k_func(t_arr[0])

            for i in range(n_steps):
                g_n1 = self._E_inv @ b_k_func(t_arr[i + 1])
                rhs = RHS_mat @ x_c + (dt / 2) * (g_n + g_n1)
                x_c = lu_solve((lu, piv), rhs)
                g_n = g_n1
                x_phasor_full[:, i + 1] = x_c

        return {
            't': t_arr,
            'x_phasor': x_phasor_full,
            'nfev': n_steps,
        }

    def solve_multi_phasor(self, harmonics: List[int] = None,
                           t_span: Tuple[float, float] = None,
                           t_eval: np.ndarray = None,
                           **solver_kwargs) -> Dict:
        """
        Solve the circuit using multi-phasor harmonic decomposition.

        Each harmonic k is solved independently:
            E * dX_k/dt = (A - j*k*omega*E) * X_k + b_k(t)

        The time-domain signal is reconstructed as:
            x(t) = sum_k Re{ X_k(t) * e^(j*k*omega*t) }

        Parameters
        ----------
        harmonics : list of int, optional
            Harmonic indices to include. Default [0, 1] (DC + fundamental).
            Use [0, 1, 2, 3] for first three harmonics, etc.
        t_span : tuple, optional
            Time span. Uses .tran if not given.
        t_eval : ndarray, optional
            Evaluation times.
        **solver_kwargs :
            Passed to solve_ivp for each harmonic.

        Returns
        -------
        dict
            't': time vector
            'harmonics': dict {k: x_phasor_k} for each harmonic
            'V(node)': reconstructed time-domain voltage for each node
            'I(branch)': reconstructed time-domain current for each branch
            'envelopes': dict {label: envelope array}
            'solver_stats': aggregated stats
        """
        if self.phasor is None:
            self.configure_phasor()

        if harmonics is None:
            harmonics = [0, 1]

        # Time span
        if t_span is None:
            tran = self.netlist.tran_params()
            if tran:
                t_span = (tran.get('t_start', 0.0), tran['t_stop'])
            else:
                raise ValueError("No .tran command; provide t_span")

        if t_eval is None:
            n_points = max(2000, min(10000, int(50000 * (t_span[1] - t_span[0]))))
            t_eval = np.linspace(t_span[0], t_span[1], n_points)

        # Solve each harmonic independently
        harmonic_results = {}
        total_nfev = 0
        for k in harmonics:
            kw = dict(solver_kwargs)  # copy per harmonic
            h_result = self._solve_single_harmonic(k, t_span, t_eval, **kw)
            harmonic_results[k] = h_result['x_phasor']
            total_nfev += h_result['nfev']

        t = t_eval
        mna = self.mna
        omega_s = self.omega_s

        # Reconstruct time-domain signals by summing harmonics
        results = {'t': t, 'harmonics': harmonic_results}
        envelopes = {}

        for i in range(mna.n_nodes):
            name = mna.node_map.name(i)
            key = f"V({name})"
            signal = np.zeros(len(t))
            for k, x_k in harmonic_results.items():
                phasor_k = x_k[i]
                # x(t) = Re{ X_k(t) * e^(j*k*omega*t) }
                signal += np.real(phasor_k * np.exp(1j * k * omega_s * t))
            results[key] = signal
            # Envelope from fundamental (k=1)
            if 1 in harmonic_results:
                envelopes[key] = np.abs(harmonic_results[1][i])

        for idx_k, bname in enumerate(mna.vsrc_names):
            key = f"I({bname})"
            row = mna.n_nodes + idx_k
            signal = np.zeros(len(t))
            for k, x_k in harmonic_results.items():
                signal += np.real(x_k[row] * np.exp(1j * k * omega_s * t))
            results[key] = signal
            if 1 in harmonic_results:
                envelopes[key] = np.abs(harmonic_results[1][row])

        for idx_k, bname in enumerate(mna.ind_names):
            key = f"I({bname})"
            row = mna.n_nodes + mna.n_vsrc + idx_k
            signal = np.zeros(len(t))
            for k, x_k in harmonic_results.items():
                signal += np.real(x_k[row] * np.exp(1j * k * omega_s * t))
            results[key] = signal
            if 1 in harmonic_results:
                envelopes[key] = np.abs(harmonic_results[1][row])

        results['envelopes'] = envelopes
        results['solver_stats'] = {'nfev': total_nfev}

        return results

    def solve_multi_phasor_fixed(self, harmonics: List[int] = None,
                                  t_span: Tuple[float, float] = None,
                                  n_steps: int = None,
                                  steps_per_period: int = 200,
                                  init_steady_state: bool = False) -> Dict:
        """
        Solve multi-phasor with fixed-step complex trapezoidal per harmonic.

        Each harmonic k gets its own LU factorisation of (I - dt/2 * M_dp_k).
        Much faster than Radau for stiff systems.

        Parameters
        ----------
        harmonics : list of int, optional
            Harmonic indices. Default [0, 1].
        t_span : tuple, optional
        n_steps : int, optional
            Steps per harmonic. Overrides steps_per_period if given.
        steps_per_period : int, optional
            Steps per carrier period for auto step-count. Default 200.

        Returns
        -------
        dict – same format as solve_multi_phasor
        """
        if self.phasor is None:
            self.configure_phasor()

        if harmonics is None:
            harmonics = [0, 1]

        if t_span is None:
            tran = self.netlist.tran_params()
            if tran:
                t_span = (tran.get('t_start', 0.0), tran['t_stop'])
            else:
                raise ValueError("No .tran command; provide t_span")

        if n_steps is None:
            period = 2 * np.pi / self.omega_s
            n_periods = (t_span[1] - t_span[0]) / period
            n_steps = max(200, int(steps_per_period * n_periods))

        dt = (t_span[1] - t_span[0]) / n_steps
        t_arr = np.linspace(t_span[0], t_span[1], n_steps + 1)

        # Solve each harmonic independently with fixed-step
        harmonic_results = {}
        total_nfev = 0
        for k in harmonics:
            h_result = self._solve_single_harmonic_fixed(
                k, t_arr, dt, init_steady_state=init_steady_state)
            harmonic_results[k] = h_result['x_phasor']
            total_nfev += h_result['nfev']

        t = t_arr
        mna = self.mna
        omega_s = self.omega_s

        # Reconstruct time-domain signals
        results = {'t': t, 'harmonics': harmonic_results}
        envelopes = {}

        for i in range(mna.n_nodes):
            name = mna.node_map.name(i)
            key = f"V({name})"
            signal = np.zeros(len(t))
            for k, x_k in harmonic_results.items():
                phasor_k = x_k[i]
                signal += np.real(phasor_k * np.exp(1j * k * omega_s * t))
            results[key] = signal
            if 1 in harmonic_results:
                envelopes[key] = np.abs(harmonic_results[1][i])

        for idx_k, bname in enumerate(mna.vsrc_names):
            key = f"I({bname})"
            row = mna.n_nodes + idx_k
            signal = np.zeros(len(t))
            for k, x_k in harmonic_results.items():
                signal += np.real(x_k[row] * np.exp(1j * k * omega_s * t))
            results[key] = signal
            if 1 in harmonic_results:
                envelopes[key] = np.abs(harmonic_results[1][row])

        for idx_k, bname in enumerate(mna.ind_names):
            key = f"I({bname})"
            row = mna.n_nodes + mna.n_vsrc + idx_k
            signal = np.zeros(len(t))
            for k, x_k in harmonic_results.items():
                signal += np.real(x_k[row] * np.exp(1j * k * omega_s * t))
            results[key] = signal
            if 1 in harmonic_results:
                envelopes[key] = np.abs(harmonic_results[1][row])

        results['envelopes'] = envelopes
        results['solver_stats'] = {
            'nfev': total_nfev,
            'njev': 0,
            'nlu': len(harmonics),
            'n_steps': n_steps,
            'dt': dt,
        }

        return results

    # ----------------------------------------------------------
    # Derived quantities
    # ----------------------------------------------------------

    def resonant_frequency(self) -> Optional[float]:
        """
        Estimate resonant frequency from L and C values.
        Returns f_r in Hz, or None if no LC pair found.
        """
        inductors = self.netlist.inductors()
        capacitors = self.netlist.capacitors()
        if inductors and capacitors:
            L = inductors[0].value
            C = capacitors[0].value
            return 1.0 / (2 * np.pi * np.sqrt(L * C))
        return None

    def quality_factor(self) -> Optional[float]:
        """Estimate Q factor for series RLC."""
        inductors = self.netlist.inductors()
        capacitors = self.netlist.capacitors()
        resistors = self.netlist.resistors()
        if inductors and capacitors and resistors:
            L = inductors[0].value
            C = capacitors[0].value
            # Use the smallest resistor as the series resistance
            R = min(r.value for r in resistors if r.value > 0)
            return (1.0 / R) * np.sqrt(L / C)
        return None

    def info(self) -> str:
        """Print circuit summary."""
        lines = [self.netlist.summary()]
        lines.append(f"\nMNA system size: {self.mna.n_total}")
        lines.append(f"  Node voltages: {self.mna.n_nodes}")
        lines.append(f"  V-source branches: {self.mna.n_vsrc}")
        lines.append(f"  Inductor branches: {self.mna.n_ind}")
        lines.append(f"  State labels: {self.mna.state_labels}")

        fr = self.resonant_frequency()
        if fr:
            lines.append(f"\nEstimated resonant freq: {fr/1e3:.2f} kHz")
        Q = self.quality_factor()
        if Q:
            lines.append(f"Estimated Q factor: {Q:.2f}")

        if self.phasor:
            lines.append(f"\nPhasor configured: omega_s = {self.omega_s:.0f} rad/s "
                         f"({self.omega_s/(2*np.pi)/1e3:.2f} kHz)")

        return '\n'.join(lines)

    def __repr__(self):
        return (f"NetlistCircuit({self.netlist.title}, "
                f"{self.mna.n_total} states, "
                f"{len(self.netlist.elements)} elements)")
