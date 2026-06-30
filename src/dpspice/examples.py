"""Access the bundled example netlists and references as *packaged resources*.

The example files live inside the importable package at
``dpspice/data/examples`` and are shipped as package data (see
``pyproject.toml``). Every consumer — :mod:`dpspice.reproduce`, the notebooks,
the tests — reaches them through this module, which resolves them with
:mod:`importlib.resources`. That means the lookup works identically in an
editable checkout, a wheel install, and a zipapp: it never depends on
``__file__``-relative or current-working-directory paths.

Public helpers::

    import dpspice
    dpspice.list_examples()                  # -> ["rlc.sp", "rectifier_rc.sp", ...]
    text = dpspice.example_text("rlc.sp")    # netlist source as a string
    with dpspice.example_path("rectifier_halfwave.raw") as p:
        ...                                  # real filesystem path (binary refs)
"""
from __future__ import annotations

from contextlib import contextmanager
from importlib import resources
from typing import Iterator, List

from .dispatch import DpspiceError

# Anchor on the top-level package and descend into the data directory. Anchoring
# on ``dpspice`` (a real package) rather than ``dpspice.data.examples`` avoids
# requiring the data directories to be import packages of their own.
_DATA_PARTS = ("data", "examples")


def _root():
    return resources.files("dpspice").joinpath(*_DATA_PARTS)


def _resource(name: str):
    if "/" in name or "\\" in name or name in ("", ".", ".."):
        raise DpspiceError(f"Invalid example name: {name!r}")
    return _root().joinpath(name)


def list_examples() -> List[str]:
    """Return the names of every bundled example file, sorted."""
    return sorted(p.name for p in _root().iterdir() if p.is_file())


def example_text(name: str) -> str:
    """Return the text of a bundled example netlist (e.g. ``"rlc.sp"``)."""
    res = _resource(name)
    if not res.is_file():
        raise DpspiceError(
            f"Bundled example not found: {name}. "
            f"Available: {', '.join(list_examples())}."
        )
    return res.read_text(encoding="utf-8")


@contextmanager
def example_path(name: str) -> Iterator[str]:
    """Yield a real filesystem path to a bundled example.

    Use this for binary references (e.g. the LTspice ``.raw``) that must be
    opened by path rather than read as text. In a zipapp install the resource
    is materialised to a temporary file for the duration of the ``with`` block.
    """
    res = _resource(name)
    if not res.is_file():
        raise DpspiceError(
            f"Bundled example not found: {name}. "
            f"Available: {', '.join(list_examples())}."
        )
    with resources.as_file(res) as path:
        yield str(path)
