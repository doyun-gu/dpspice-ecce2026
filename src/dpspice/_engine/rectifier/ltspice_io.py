"""
Minimal LTspice .raw reader for the rectifier cross-check (build spec M6).

Handles the binary "real double" transient format LTspice writes on macOS.
Returns time + named traces; the solver/notebook side then folds onto one
period and resamples to the HB phase grid via metrics.fold_to_period.
"""
import numpy as np


def read_raw(path):
    """Parse an LTspice binary .raw (real double transient).
    Returns (names, data) where data has shape (n_points, n_vars), column 0
    is time.  Variable names are lower-cased, e.g. 'time', 'v(out)', 'i(d1)'."""
    with open(path, "rb") as f:
        raw = f.read()
    # LTspice writes the header as UTF-16LE on macOS, UTF-8/latin-1 elsewhere.
    enc, marker = None, None
    for e in ("utf-16-le", "latin-1"):
        m = "Binary:\n".encode(e)
        i = raw.find(m)
        if i >= 0:
            enc, marker, hdr_end = e, m, i
            break
    if enc is None:
        raise ValueError("not a binary LTspice raw (no 'Binary:' marker)")
    header = raw[:hdr_end].decode(enc, errors="ignore")

    n_vars = n_pts = None
    names = []
    in_vars = False
    for line in header.splitlines():
        s = line.strip()
        if s.startswith("No. Variables:"):
            n_vars = int(s.split(":")[1])
        elif s.startswith("No. Points:"):
            n_pts = int(s.split(":")[1])
        elif s.startswith("Variables:"):
            in_vars = True
        elif in_vars:
            parts = s.split()
            if len(parts) >= 2 and parts[0].isdigit():
                names.append(parts[1].lower())
            if len(names) == n_vars:
                in_vars = False

    body = raw[hdr_end + len(marker):]
    vals = np.frombuffer(body, dtype="<f8", count=n_vars * n_pts)
    data = vals.reshape(n_pts, n_vars)
    # LTspice stores the time column as |t| with sign tricks; take abs to be safe
    data = data.copy()
    data[:, 0] = np.abs(data[:, 0])
    return names, data


def trace(names, data, name):
    """Fetch a named trace (case-insensitive), e.g. 'V(out)' or 'I(D1)'."""
    return data[:, names.index(name.lower())]
