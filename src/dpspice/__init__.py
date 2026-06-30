"""DPSpice — topology-independent dynamic-phasor circuit simulation (ECCE 2026).

The public API lives in :mod:`dpspice.api` and is re-exported here:

    import dpspice
    ckt = dpspice.load("circuit.sp")
    result = ckt.run()

Importing this package is side-effect-free: it prints nothing, needs no
terminal, and starts no solve. Interactive chrome (banners, spinners) lives
only in the CLI layer (:mod:`dpspice.cli`).
"""
__version__ = "1.0.1"

from .api import (  # noqa: E402
    load,
    run,
    info,
    validate,
    backend,
    Circuit,
    Result,
    Validation,
)
from .dispatch import DpspiceError  # noqa: E402
from .examples import (  # noqa: E402
    list_examples,
    example_text,
    example_path,
)

__all__ = [
    "__version__",
    "load",
    "run",
    "info",
    "validate",
    "backend",
    "Circuit",
    "Result",
    "Validation",
    "DpspiceError",
    "list_examples",
    "example_text",
    "example_path",
]
