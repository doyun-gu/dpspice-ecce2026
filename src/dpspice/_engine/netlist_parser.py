"""
LTspice Netlist Parser for Dynamic Phasor Framework.

Parses standard SPICE / LTspice netlist files (.net, .cir, .sp) and
converts them into an internal circuit representation that the dynamic
phasor solver can use.

Supported elements:
    R  - Resistor           R<name> <n+> <n-> <value>
    L  - Inductor           L<name> <n+> <n-> <value> [IC=<i0>]
    C  - Capacitor          C<name> <n+> <n-> <value> [IC=<v0>]
    V  - Voltage source     V<name> <n+> <n-> [DC <val>] [SINE(...)] [PULSE(...)] [PWL(...)]
    I  - Current source     I<name> <n+> <n-> [DC <val>] [SINE(...)] [PULSE(...)]
    K  - Coupled inductors  K<name> <L1> <L2> <coupling>

Supported dot commands:
    .param  - Parameter definitions
    .tran   - Transient analysis
    .ac     - AC analysis
    .ic     - Initial conditions
    .step   - Parameter sweep
    .model  - (parsed but not fully interpreted yet)

LTspice suffix conventions:
    T=1e12, G=1e9, Meg=1e6, k=1e3, m=1e-3, u/u=1e-6, n=1e-9, p=1e-12, f=1e-15

Author: Doyun Gu (University of Manchester)
"""

import re
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Callable, Any
from enum import Enum
from simpleeval import simple_eval


# ----------------------------------------------------------------------
# SPICE value parsing
# ----------------------------------------------------------------------

# SPICE engineering suffixes (case-insensitive, except Meg vs m)
_SUFFIX_MAP = {
    'T':   1e12,
    'G':   1e9,
    'MEG': 1e6,
    'K':   1e3,
    'M':   1e-3,   # SPICE convention: M = milli
    'MIL': 25.4e-6,
    'U':   1e-6,
    'N':   1e-9,
    'P':   1e-12,
    'F':   1e-15,
}

# Regex for a floating-point number possibly followed by a suffix
_VALUE_RE = re.compile(
    r'^([+-]?\d+\.?\d*(?:[eE][+-]?\d+)?)\s*'  # numeric part
    r'(T|G|MEG|Meg|meg|K|k|MIL|mil|M|m|U|u|N|n|P|p|F|f)?'  # suffix
    r'(\S*)',  # trailing unit label (Ohm, H, F, etc.) - ignored
    re.IGNORECASE
)


def parse_spice_value(text: str, params: Dict[str, float] = None) -> float:
    """
    Parse a SPICE value string into a float.

    Handles:
        - Plain numbers: "100", "3.14", "1e-6"
        - Suffixed numbers: "100u", "30.07n", "2k", "1Meg"
        - Parameter references: "{fres}", "Rval"
        - Expressions with braces: "{1/(2*pi*sqrt(100u*100n))}"

    Parameters
    ----------
    text : str
        SPICE value string
    params : dict, optional
        Parameter name -> value mapping for .param substitution

    Returns
    -------
    float
        Parsed numeric value

    Raises
    ------
    ValueError
        If the string cannot be parsed
    """
    text = text.strip()
    if not text:
        raise ValueError("Empty value string")

    params = params or {}

    # Handle brace expressions: {expr}
    if text.startswith('{') and text.endswith('}'):
        return _eval_param_expr(text[1:-1], params)

    # Try direct parameter lookup
    if text in params:
        return params[text]

    # Try numeric parse
    m = _VALUE_RE.match(text)
    if m:
        num_str, suffix, _ = m.groups()
        value = float(num_str)
        if suffix:
            suffix_upper = suffix.upper()
            # Special handling: 'Meg'/'MEG' vs 'M'/'m'
            if suffix_upper == 'M' and suffix in ('M', 'm'):
                multiplier = 1e-3  # milli
            elif suffix_upper == 'MEG':
                multiplier = 1e6
            elif suffix_upper == 'K':
                multiplier = 1e3
            else:
                multiplier = _SUFFIX_MAP.get(suffix_upper, 1.0)
            value *= multiplier
        return value

    # Last resort: try eval with params + math
    return _eval_param_expr(text, params)


def _eval_param_expr(expr: str, params: Dict[str, float]) -> float:
    """Safely evaluate a parameter expression."""
    names = {
        'pi': math.pi,
        'PI': math.pi,
        'e': math.e,
    }
    names.update(params)
    functions = {
        'sqrt': math.sqrt,
        'sin': math.sin,
        'cos': math.cos,
        'tan': math.tan,
        'exp': math.exp,
        'log': math.log,
        'log10': math.log10,
        'abs': abs,
        'pow': pow,
    }
    try:
        return float(simple_eval(expr, names=names, functions=functions))
    except Exception as exc:
        raise ValueError(f"Cannot evaluate expression: '{expr}'") from exc


# ----------------------------------------------------------------------
# Source waveform specifications
# ----------------------------------------------------------------------

class SourceType(Enum):
    DC = "DC"
    SINE = "SINE"
    PULSE = "PULSE"
    PWL = "PWL"
    AC = "AC"


@dataclass
class SourceSpec:
    """Parsed source waveform specification."""
    source_type: SourceType = SourceType.DC
    dc_value: float = 0.0

    # SINE(Voff Vamp Freq [Td] [Theta] [Phi] [Ncycles])
    sine_offset: float = 0.0
    sine_amplitude: float = 0.0
    sine_freq: float = 0.0       # Hz
    sine_delay: float = 0.0
    sine_damping: float = 0.0    # Theta (1/s)
    sine_phase: float = 0.0      # degrees

    # PULSE(V1 V2 Tdelay Trise Tfall Ton Tperiod [Ncycles])
    pulse_v1: float = 0.0
    pulse_v2: float = 0.0
    pulse_delay: float = 0.0
    pulse_rise: float = 0.0
    pulse_fall: float = 0.0
    pulse_on: float = 0.0
    pulse_period: float = 0.0

    # PWL(t1 v1 t2 v2 ...)
    pwl_points: List[Tuple[float, float]] = field(default_factory=list)

    # AC magnitude and phase (for .ac analysis)
    ac_mag: float = 0.0
    ac_phase: float = 0.0

    def time_function(self) -> Callable[[float], float]:
        """
        Return a callable f(t) -> voltage/current for time-domain simulation.
        """
        if self.source_type == SourceType.DC:
            dc = self.dc_value
            return lambda t: dc

        elif self.source_type == SourceType.SINE:
            off = self.sine_offset
            amp = self.sine_amplitude
            freq = self.sine_freq
            td = self.sine_delay
            theta = self.sine_damping
            phi_rad = math.radians(self.sine_phase)

            def _sine(t):
                if t < td:
                    return off
                t_eff = t - td
                return off + amp * math.sin(2 * math.pi * freq * t_eff + phi_rad) * math.exp(-theta * t_eff)
            return _sine

        elif self.source_type == SourceType.PULSE:
            v1, v2 = self.pulse_v1, self.pulse_v2
            td = self.pulse_delay
            tr, tf = self.pulse_rise, self.pulse_fall
            ton, per = self.pulse_on, self.pulse_period

            def _pulse(t):
                if t < td:
                    return v1
                if per <= 0:
                    return v1
                tc = (t - td) % per  # position in cycle
                if tc < tr:
                    return v1 + (v2 - v1) * tc / max(tr, 1e-30)
                elif tc < tr + ton:
                    return v2
                elif tc < tr + ton + tf:
                    return v2 + (v1 - v2) * (tc - tr - ton) / max(tf, 1e-30)
                else:
                    return v1
            return _pulse

        elif self.source_type == SourceType.PWL:
            pts = sorted(self.pwl_points, key=lambda p: p[0])
            times = [p[0] for p in pts]
            vals = [p[1] for p in pts]

            def _pwl(t):
                if t <= times[0]:
                    return vals[0]
                if t >= times[-1]:
                    return vals[-1]
                for i in range(len(times) - 1):
                    if times[i] <= t <= times[i + 1]:
                        frac = (t - times[i]) / (times[i + 1] - times[i])
                        return vals[i] + frac * (vals[i + 1] - vals[i])
                return vals[-1]
            return _pwl

        else:
            return lambda t: 0.0

    def omega(self) -> float:
        """Angular frequency for SINE sources (rad/s)."""
        if self.source_type == SourceType.SINE:
            return 2 * math.pi * self.sine_freq
        return 0.0


def _parse_source_spec(tokens: List[str], params: Dict[str, float]) -> SourceSpec:
    """
    Parse source specification tokens after the node declarations.

    Handles forms like:
        DC 5
        SINE(0 10 50k)
        SINE(0 10 50k 0 0 90)
        PULSE(0 5 0 10n 10n 5u 10u)
        PWL(0 0 1m 5 2m 0)
        AC 1 0
        12          (treated as DC)
    """
    spec = SourceSpec()
    if not tokens:
        return spec

    # Rejoin tokens so we can deal with parenthesised groups
    text = ' '.join(tokens)

    # -- DC --------------------------------------------------------
    dc_match = re.match(r'(?:DC\s+)?([+-]?\d+\.?\d*(?:[eE][+-]?\d+)?[a-zA-Z]*)\s*$',
                        text, re.IGNORECASE)
    if dc_match and 'SINE' not in text.upper() and 'PULSE' not in text.upper():
        # Might just be a bare number
        try:
            spec.source_type = SourceType.DC
            spec.dc_value = parse_spice_value(dc_match.group(1), params)
            return spec
        except ValueError:
            pass

    # -- SINE ------------------------------------------------------
    sine_match = re.search(r'SINE\s*\(([^)]*)\)', text, re.IGNORECASE)
    if sine_match:
        args = sine_match.group(1).split()
        vals = [parse_spice_value(a, params) for a in args]
        spec.source_type = SourceType.SINE
        if len(vals) >= 1: spec.sine_offset = vals[0]
        if len(vals) >= 2: spec.sine_amplitude = vals[1]
        if len(vals) >= 3: spec.sine_freq = vals[2]
        if len(vals) >= 4: spec.sine_delay = vals[3]
        if len(vals) >= 5: spec.sine_damping = vals[4]
        if len(vals) >= 6: spec.sine_phase = vals[5]
        # Also check for DC part before SINE
        dc_part = text[:sine_match.start()].strip()
        if dc_part:
            dc_m = re.match(r'DC\s+(\S+)', dc_part, re.IGNORECASE)
            if dc_m:
                spec.dc_value = parse_spice_value(dc_m.group(1), params)
        return spec

    # -- PULSE -----------------------------------------------------
    pulse_match = re.search(r'PULSE\s*\(([^)]*)\)', text, re.IGNORECASE)
    if pulse_match:
        args = pulse_match.group(1).split()
        vals = [parse_spice_value(a, params) for a in args]
        spec.source_type = SourceType.PULSE
        if len(vals) >= 1: spec.pulse_v1 = vals[0]
        if len(vals) >= 2: spec.pulse_v2 = vals[1]
        if len(vals) >= 3: spec.pulse_delay = vals[2]
        if len(vals) >= 4: spec.pulse_rise = vals[3]
        if len(vals) >= 5: spec.pulse_fall = vals[4]
        if len(vals) >= 6: spec.pulse_on = vals[5]
        if len(vals) >= 7: spec.pulse_period = vals[6]
        return spec

    # -- PWL -------------------------------------------------------
    pwl_match = re.search(r'PWL\s*\(([^)]*)\)', text, re.IGNORECASE)
    if pwl_match:
        args = pwl_match.group(1).split()
        vals = [parse_spice_value(a, params) for a in args]
        spec.source_type = SourceType.PWL
        spec.pwl_points = [(vals[i], vals[i + 1]) for i in range(0, len(vals) - 1, 2)]
        return spec

    # -- AC --------------------------------------------------------
    ac_match = re.match(r'AC\s+(\S+)\s*(\S+)?', text, re.IGNORECASE)
    if ac_match:
        spec.source_type = SourceType.AC
        spec.ac_mag = parse_spice_value(ac_match.group(1), params)
        if ac_match.group(2):
            spec.ac_phase = parse_spice_value(ac_match.group(2), params)
        return spec

    # -- Fallback: try bare numeric as DC --------------------------
    try:
        spec.source_type = SourceType.DC
        spec.dc_value = parse_spice_value(text.split()[0], params)
    except ValueError:
        pass

    return spec


# ----------------------------------------------------------------------
# Parsed element dataclasses
# ----------------------------------------------------------------------

@dataclass
class NetlistElement:
    """A single parsed circuit element."""
    prefix: str         # 'R', 'L', 'C', 'V', 'I', 'D', 'M', 'Q', 'X', 'K'
    name: str           # Full name e.g. 'R1', 'L_series'
    nodes: List[str]    # Node names
    value: float = 0.0  # Primary value (ohms, henries, farads, etc.)
    model: str = ""     # Model name (for D, M, Q, X)
    params: Dict[str, float] = field(default_factory=dict)
    source_spec: Optional[SourceSpec] = None
    ic: Optional[float] = None  # Initial condition

    def __repr__(self):
        nodes_str = ' '.join(self.nodes)
        if self.prefix in ('V', 'I') and self.source_spec:
            return f"{self.name} {nodes_str} [{self.source_spec.source_type.value}]"
        return f"{self.name} {nodes_str} = {self.value}"


@dataclass
class AnalysisCommand:
    """A parsed SPICE analysis command."""
    analysis_type: str    # 'tran', 'ac', 'dc', 'op'
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedNetlist:
    """
    Complete parsed netlist representation.

    Attributes
    ----------
    title : str
        First line / title of the netlist
    elements : list of NetlistElement
        All circuit elements
    analyses : list of AnalysisCommand
        All analysis commands
    params : dict
        Global .param definitions
    initial_conditions : dict
        .ic node voltage specs
    models : dict
        .model definitions (raw text)
    subcircuits : dict
        .subckt definitions (raw text)
    nodes : set
        All node names found in the netlist
    """
    title: str = ""
    elements: List[NetlistElement] = field(default_factory=list)
    analyses: List[AnalysisCommand] = field(default_factory=list)
    params: Dict[str, float] = field(default_factory=dict)
    initial_conditions: Dict[str, float] = field(default_factory=dict)
    models: Dict[str, str] = field(default_factory=dict)
    subcircuits: Dict[str, str] = field(default_factory=dict)
    nodes: set = field(default_factory=set)

    # Convenience accessors
    def resistors(self) -> List[NetlistElement]:
        return [e for e in self.elements if e.prefix == 'R']

    def inductors(self) -> List[NetlistElement]:
        return [e for e in self.elements if e.prefix == 'L']

    def capacitors(self) -> List[NetlistElement]:
        return [e for e in self.elements if e.prefix == 'C']

    def voltage_sources(self) -> List[NetlistElement]:
        return [e for e in self.elements if e.prefix == 'V']

    def current_sources(self) -> List[NetlistElement]:
        return [e for e in self.elements if e.prefix == 'I']

    def coupled_inductors(self) -> List[NetlistElement]:
        return [e for e in self.elements if e.prefix == 'K']

    def transformers(self) -> List[NetlistElement]:
        return [e for e in self.elements if e.prefix == 'T']

    def get_element(self, name: str) -> Optional[NetlistElement]:
        """Find element by name (case-insensitive)."""
        name_lower = name.lower()
        for e in self.elements:
            if e.name.lower() == name_lower:
                return e
        return None

    def non_ground_nodes(self) -> List[str]:
        """Return sorted list of non-ground node names."""
        ground_aliases = {'0', 'gnd', 'GND', 'ground'}
        return sorted(self.nodes - ground_aliases)

    def tran_params(self) -> Optional[Dict[str, Any]]:
        """Return .tran parameters if present."""
        for a in self.analyses:
            if a.analysis_type == 'tran':
                return a.params
        return None

    def summary(self) -> str:
        """Return a human-readable summary."""
        lines = [f"Netlist: {self.title}"]
        lines.append(f"  Nodes: {len(self.non_ground_nodes())} (excl. ground)")
        lines.append(f"  Elements: {len(self.elements)}")
        for prefix, label in [('R', 'Resistors'), ('L', 'Inductors'),
                               ('C', 'Capacitors'), ('V', 'V-sources'),
                               ('I', 'I-sources'), ('K', 'Coupled')]:
            elems = [e for e in self.elements if e.prefix == prefix]
            if elems:
                lines.append(f"    {label}: {len(elems)}")
        for a in self.analyses:
            lines.append(f"  Analysis: .{a.analysis_type} {a.params}")
        if self.params:
            lines.append(f"  Parameters: {self.params}")
        return '\n'.join(lines)


# ----------------------------------------------------------------------
# Main parser
# ----------------------------------------------------------------------

class LTSpiceNetlistParser:
    """
    Parser for LTspice / SPICE netlist files.

    Usage
    -----
    >>> parser = LTSpiceNetlistParser()
    >>> netlist = parser.parse_string(netlist_text)
    >>> print(netlist.summary())
    """

    def __init__(self):
        self._params: Dict[str, float] = {}

    # File I/O removed for browser compatibility
    # def parse_file(self, filepath: str) -> ParsedNetlist:
    #     """Parse a netlist file."""
    #     with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
    #         text = f.read()
    #     return self.parse_string(text)

    def parse_string(self, text: str) -> ParsedNetlist:
        """Parse a netlist from a string."""
        self._params = {}
        self._raw_title = ""
        result = ParsedNetlist()

        # Pre-process: handle line continuations (+ at start) and strip comments
        lines = self._preprocess(text)

        if lines:
            # First non-empty line may be the title
            first = lines[0]
            if not first.startswith('.') and not self._is_element_line(first):
                result.title = first
                lines = lines[1:]
            elif self._raw_title:
                result.title = self._raw_title
        elif self._raw_title:
            result.title = self._raw_title

        # First pass: collect .param definitions
        for line in lines:
            if line.lower().startswith('.param'):
                self._parse_param(line)

        result.params = dict(self._params)

        # Second pass: parse everything
        for line in lines:
            lower = line.lower().strip()

            if lower.startswith('.'):
                self._parse_dot_command(line, result)
            elif lower == '.end':
                break
            elif self._is_element_line(line):
                elem = self._parse_element(line)
                if elem:
                    result.elements.append(elem)
                    # Collect nodes
                    for node in elem.nodes:
                        result.nodes.add(node)

        return result

    # -------- Pre-processing ------------------------------------------

    def _preprocess(self, text: str) -> List[str]:
        """Join continuation lines and strip comments."""
        raw_lines = text.splitlines()
        joined = []
        self._raw_title = ""  # Capture first comment as title

        for i, raw in enumerate(raw_lines):
            stripped = raw.rstrip()
            # Remove inline comments (;)
            comment_pos = stripped.find(';')
            if comment_pos >= 0:
                stripped = stripped[:comment_pos].rstrip()
            # First non-empty line starting with * is the title
            if stripped.startswith('*') and not self._raw_title:
                self._raw_title = stripped[1:].strip()
                continue
            # Skip other full-line comments
            if stripped.startswith('*'):
                continue
            if not stripped:
                continue
            # Continuation line
            if stripped.startswith('+') and joined:
                joined[-1] += ' ' + stripped[1:].lstrip()
            else:
                joined.append(stripped)
        return joined

    def _is_element_line(self, line: str) -> bool:
        """Check if a line starts with a component prefix letter."""
        if not line:
            return False
        return line[0].upper() in 'RLCVIDEQMXKBSTW'

    # -------- Element parsing -----------------------------------------

    def _parse_element(self, line: str) -> Optional[NetlistElement]:
        """Parse a single component line."""
        tokens = line.split()
        if not tokens:
            return None

        name = tokens[0]
        prefix = name[0].upper()

        if prefix == 'R':
            return self._parse_RLC(tokens, 'R')
        elif prefix == 'L':
            return self._parse_RLC(tokens, 'L')
        elif prefix == 'C':
            return self._parse_RLC(tokens, 'C')
        elif prefix == 'V':
            return self._parse_source(tokens, 'V')
        elif prefix == 'I':
            return self._parse_source(tokens, 'I')
        elif prefix == 'K':
            return self._parse_coupling(tokens)
        elif prefix in ('D', 'Q', 'M', 'X'):
            return self._parse_semiconductor(tokens, prefix)
        else:
            # Unknown element - store raw
            return NetlistElement(
                prefix=prefix, name=name,
                nodes=tokens[1:3] if len(tokens) >= 3 else [],
            )

    def _parse_RLC(self, tokens: List[str], prefix: str) -> NetlistElement:
        """Parse R, L, or C element."""
        # Format: R1 N001 N002 1k [params...]
        name = tokens[0]
        n_plus = tokens[1] if len(tokens) > 1 else '0'
        n_minus = tokens[2] if len(tokens) > 2 else '0'

        # Value token - may be followed by IC=, tc=, etc.
        value = 0.0
        ic = None
        extra_params = {}

        for tok in tokens[3:]:
            tok_upper = tok.upper()
            if tok_upper.startswith('IC='):
                ic = parse_spice_value(tok[3:], self._params)
            elif '=' in tok:
                k, v = tok.split('=', 1)
                try:
                    extra_params[k.lower()] = parse_spice_value(v, self._params)
                except ValueError:
                    extra_params[k.lower()] = v
            elif value == 0.0:
                try:
                    value = parse_spice_value(tok, self._params)
                except ValueError:
                    pass  # might be a model name

        return NetlistElement(
            prefix=prefix, name=name,
            nodes=[n_plus, n_minus],
            value=value, ic=ic, params=extra_params,
        )

    def _parse_source(self, tokens: List[str], prefix: str) -> NetlistElement:
        """Parse V or I source."""
        name = tokens[0]
        n_plus = tokens[1] if len(tokens) > 1 else '0'
        n_minus = tokens[2] if len(tokens) > 2 else '0'

        # Everything after the nodes is the source spec
        spec_tokens = tokens[3:]
        source_spec = _parse_source_spec(spec_tokens, self._params)

        return NetlistElement(
            prefix=prefix, name=name,
            nodes=[n_plus, n_minus],
            source_spec=source_spec,
        )

    def _parse_coupling(self, tokens: List[str]) -> NetlistElement:
        """Parse K (coupled inductor) statement: K1 L1 L2 0.99"""
        name = tokens[0]
        l1_name = tokens[1] if len(tokens) > 1 else ''
        l2_name = tokens[2] if len(tokens) > 2 else ''
        value = 0.0
        if len(tokens) > 3:
            try:
                value = parse_spice_value(tokens[3], self._params)
            except ValueError:
                pass

        return NetlistElement(
            prefix='K', name=name,
            nodes=[l1_name, l2_name],  # these are inductor names, not nodes
            value=value,
        )

    def _parse_semiconductor(self, tokens: List[str], prefix: str) -> NetlistElement:
        """Parse D, Q, M, X elements (basic handling)."""
        name = tokens[0]
        # Number of nodes varies: D=2, Q=3(+sub), M=4, X=variable
        if prefix == 'D':
            nodes = tokens[1:3]
            model = tokens[3] if len(tokens) > 3 else ''
        elif prefix == 'Q':
            nodes = tokens[1:4]
            model = tokens[4] if len(tokens) > 4 else ''
        elif prefix == 'M':
            nodes = tokens[1:5]
            model = tokens[5] if len(tokens) > 5 else ''
        else:  # X - subcircuit
            # Last token before params is the subcircuit name
            # Nodes are everything between name and model
            model = tokens[-1]  # approximate
            nodes = tokens[1:-1]

        return NetlistElement(
            prefix=prefix, name=name,
            nodes=nodes, model=model,
        )

    # -------- Dot-command parsing -------------------------------------

    def _parse_dot_command(self, line: str, result: ParsedNetlist):
        """Parse a dot command."""
        tokens = line.split()
        cmd = tokens[0].lower()

        if cmd == '.param':
            # Already parsed in first pass
            pass

        elif cmd == '.tran':
            result.analyses.append(self._parse_tran(tokens))

        elif cmd == '.ac':
            result.analyses.append(self._parse_ac(tokens))

        elif cmd == '.dc':
            result.analyses.append(self._parse_dc(tokens))

        elif cmd == '.op':
            result.analyses.append(AnalysisCommand(analysis_type='op'))

        elif cmd == '.ic':
            self._parse_ic(tokens, result)

        elif cmd == '.model':
            if len(tokens) >= 3:
                model_name = tokens[1]
                result.models[model_name] = ' '.join(tokens[2:])

        elif cmd == '.step':
            result.analyses.append(self._parse_step(tokens))

        elif cmd == '.lib' or cmd == '.include':
            pass  # We don't follow external files

        elif cmd == '.meas' or cmd == '.measure':
            pass  # Measurement - not needed for simulation

        elif cmd == '.subckt':
            pass  # TODO: full subcircuit support

        elif cmd == '.ends':
            pass

        elif cmd == '.backanno' or cmd == '.end':
            pass

    def _parse_param(self, line: str):
        """Parse .param definitions: .param Rval=1k fres=50k"""
        # Remove .param prefix
        rest = re.sub(r'^\.param\s+', '', line, flags=re.IGNORECASE).strip()
        # Split on spaces, handling multiple params per line
        assignments = re.findall(r'(\w+)\s*=\s*(\{[^}]+\}|\S+)', rest)
        for name, value_str in assignments:
            try:
                self._params[name] = parse_spice_value(value_str, self._params)
            except ValueError:
                pass  # might be forward-referenced; ignore for now

    def _parse_tran(self, tokens: List[str]) -> AnalysisCommand:
        """Parse .tran Tstop or .tran Tstep Tstop [Tstart] [Tmaxstep] [options]"""
        params = {}
        # Filter out option flags
        vals = [t for t in tokens[1:] if not t.startswith('startup')
                and not t.startswith('uic') and '=' not in t.lower()
                and t.lower() != 'steady']

        try:
            if len(vals) == 1:
                params['t_stop'] = parse_spice_value(vals[0], self._params)
                params['t_step'] = 0.0
            elif len(vals) >= 2:
                params['t_step'] = parse_spice_value(vals[0], self._params)
                params['t_stop'] = parse_spice_value(vals[1], self._params)
            if len(vals) >= 3:
                params['t_start'] = parse_spice_value(vals[2], self._params)
            if len(vals) >= 4:
                params['t_maxstep'] = parse_spice_value(vals[3], self._params)
        except ValueError:
            pass

        # Check for UIC flag
        if any('uic' in t.lower() for t in tokens):
            params['uic'] = True

        return AnalysisCommand(analysis_type='tran', params=params)

    def _parse_ac(self, tokens: List[str]) -> AnalysisCommand:
        """.ac dec 100 1 1Meg"""
        params = {}
        if len(tokens) >= 2:
            params['variation'] = tokens[1].lower()  # dec, oct, lin
        if len(tokens) >= 3:
            params['n_points'] = int(parse_spice_value(tokens[2], self._params))
        if len(tokens) >= 4:
            params['f_start'] = parse_spice_value(tokens[3], self._params)
        if len(tokens) >= 5:
            params['f_stop'] = parse_spice_value(tokens[4], self._params)
        return AnalysisCommand(analysis_type='ac', params=params)

    def _parse_dc(self, tokens: List[str]) -> AnalysisCommand:
        """.dc V1 0 12 0.1"""
        params = {}
        if len(tokens) >= 2: params['source'] = tokens[1]
        if len(tokens) >= 3: params['start'] = parse_spice_value(tokens[2], self._params)
        if len(tokens) >= 4: params['stop'] = parse_spice_value(tokens[3], self._params)
        if len(tokens) >= 5: params['step'] = parse_spice_value(tokens[4], self._params)
        return AnalysisCommand(analysis_type='dc', params=params)

    def _parse_ic(self, tokens: List[str], result: ParsedNetlist):
        """.ic V(N001)=5 V(N002)=0"""
        for tok in tokens[1:]:
            m = re.match(r'V\((\w+)\)\s*=\s*(\S+)', tok, re.IGNORECASE)
            if m:
                node = m.group(1)
                val = parse_spice_value(m.group(2), self._params)
                result.initial_conditions[node] = val

    def _parse_step(self, tokens: List[str]) -> AnalysisCommand:
        """.step param Rval 1k 10k 1k  OR  .step param Rval list 1k 2k 3k"""
        params = {}
        if len(tokens) >= 3 and tokens[1].lower() == 'param':
            params['param_name'] = tokens[2]
            # Check for "list" keyword: .step param <name> list <v1> <v2> ...
            if len(tokens) >= 4 and tokens[3].lower() == 'list':
                params['sweep_type'] = 'list'
                values = []
                for tok in tokens[4:]:
                    try:
                        values.append(parse_spice_value(tok, self._params))
                    except ValueError:
                        break
                params['values'] = values
                # Use the first value as the default parameter value
                if values:
                    self._params[tokens[2]] = values[0]
            else:
                # Linear sweep: .step param <name> <start> <stop> <step>
                params['sweep_type'] = 'linear'
                if len(tokens) >= 4: params['start'] = parse_spice_value(tokens[3], self._params)
                if len(tokens) >= 5: params['stop'] = parse_spice_value(tokens[4], self._params)
                if len(tokens) >= 6: params['step'] = parse_spice_value(tokens[5], self._params)
        return AnalysisCommand(analysis_type='step', params=params)


# ----------------------------------------------------------------------
# Convenience function
# ----------------------------------------------------------------------

def parse_ltspice_netlist(source: str) -> ParsedNetlist:
    """
    Parse an LTspice netlist from a string.

    Parameters
    ----------
    source : str
        A raw netlist string.

    Returns
    -------
    ParsedNetlist
        Fully parsed netlist structure.
    """
    # File I/O removed for browser compatibility
    # Previously this function would check os.path.isfile(source) and
    # read from disk. In the browser environment, only string parsing
    # is supported.
    parser = LTSpiceNetlistParser()
    return parser.parse_string(source)
