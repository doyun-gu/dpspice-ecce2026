"""Shared, professional plotting style for DPSpice notebooks and figures.

A small, dependency-light house style so every figure and table in the
notebooks (and the README hero image) shares one coherent look: muted,
instrument-grade colours with a teal accent, clean grids, no chartjunk.

Requires the ``viz`` extra (matplotlib + pandas)::

    pip install 'dpspice[viz]'

Typical use at the top of a notebook::

    %config InlineBackend.figure_format = "retina"
    from dpspice.plotting import use_style, PALETTE, style_table
    import pandas as pd
    use_style()

Then plot as usual; the palette and rcParams are applied globally. Use
``PALETTE`` for explicit series colours and :func:`style_table` to render a
clean, consistently-styled pandas table.
"""
from __future__ import annotations

from typing import Mapping, Sequence

try:
    import matplotlib as mpl
    import matplotlib.pyplot as plt  # noqa: F401  (re-exported convenience)
except ModuleNotFoundError as exc:  # viz extra not installed
    raise SystemExit(
        f"dpspice.plotting needs the plotting extra (missing: {exc.name}). "
        f"Install it with:  pip install 'dpspice[viz]'"
    )

__all__ = ["PALETTE", "CYCLE", "use_style", "style_table"]

# --- Palette -----------------------------------------------------------------
# Muted, professional, PSS/E-adjacent. Teal is the DPSpice accent (matches the
# README hero figure); ink is the neutral reference/primary line.
PALETTE = {
    "teal":   "#3fa796",  # IDP / envelope / brand accent
    "ink":    "#2b3640",  # classical / reference / primary text
    "clay":   "#cf8a5b",  # secondary accent
    "slate":  "#5b7c99",  # tertiary accent
    "plum":   "#8c6a9c",  # quaternary accent
    "muted":  "#8a949c",  # spines, secondary text
    "grid":   "#dfe4e8",  # gridlines
    "band":   "#f2f5f6",  # zebra row / soft fill
    "paper":  "#ffffff",
}

# Default series order for multi-line plots.
CYCLE = [PALETTE["teal"], PALETTE["ink"], PALETTE["clay"],
         PALETTE["slate"], PALETTE["plum"], PALETTE["muted"]]


def use_style() -> None:
    """Apply the DPSpice house style to matplotlib's global rcParams.

    High-DPI (crisp on screen and on GitHub), a consistent figure size, the
    teal-led palette, subtle grids behind the data, and clean spines.
    Idempotent; call once near the top of a notebook.
    """
    mpl.rcParams.update({
        # canvas — high-DPI for crisp rendering
        "figure.figsize":   (7.0, 4.0),
        "figure.dpi":       150,
        "savefig.dpi":      200,
        "figure.facecolor": PALETTE["paper"],
        "savefig.bbox":     "tight",
        "savefig.facecolor": PALETTE["paper"],
        # fonts
        "font.family":      "sans-serif",
        "font.sans-serif":  ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
        "font.size":        11,
        "axes.titlesize":   12.5,
        "axes.titleweight": "bold",
        "axes.titlecolor":  PALETTE["ink"],
        "axes.titlepad":    9,
        "axes.labelsize":   10.5,
        "axes.labelcolor":  PALETTE["ink"],
        # axes / spines
        "axes.facecolor":   PALETTE["paper"],
        "axes.edgecolor":   PALETTE["muted"],
        "axes.linewidth":   0.8,
        "axes.spines.top":   False,
        "axes.spines.right": False,
        "axes.axisbelow":   True,            # grid sits behind the data
        "axes.prop_cycle":  mpl.cycler(color=CYCLE),
        # grid
        "axes.grid":        True,
        "grid.color":       PALETTE["grid"],
        "grid.linewidth":   0.8,
        "grid.alpha":       1.0,
        # ticks
        "xtick.color":      PALETTE["muted"],
        "ytick.color":      PALETTE["muted"],
        "xtick.labelcolor": PALETTE["ink"],
        "ytick.labelcolor": PALETTE["ink"],
        "xtick.labelsize":  9.5,
        "ytick.labelsize":  9.5,
        "xtick.direction":  "out",
        "ytick.direction":  "out",
        # lines
        "lines.linewidth":  1.7,
        "lines.solid_capstyle": "round",
        "lines.markersize": 5.5,
        # legend
        "legend.frameon":    True,
        "legend.framealpha": 0.96,
        "legend.edgecolor":  PALETTE["grid"],
        "legend.facecolor":  PALETTE["paper"],
        "legend.fontsize":   9.5,
        "legend.borderpad":  0.6,
        "legend.handlelength": 1.8,
    })


def style_table(df,
                caption: str | None = None,
                formats: Mapping[str, str] | None = None,
                right: Sequence[str] | None = None,
                left: Sequence[str] | None = None):
    """Return a consistently-styled pandas ``Styler`` for a DataFrame.

    One coherent table look across all notebooks: teal header band, zebra
    body, left-aligned labels and right-aligned numbers, a left caption. The
    returned Styler renders as native HTML in Jupyter and on GitHub.

    Parameters
    ----------
    df       : the DataFrame to display.
    caption  : optional caption shown above the table.
    formats  : per-column format strings, e.g. ``{"NRMSE": "{:.3e}"}``.
    right/left : column names to force right/left alignment. By default
        numeric columns are right-aligned and the rest left-aligned.
    """
    import pandas as pd  # lazy: tables need pandas, plain plots do not

    num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    right = list(num_cols if right is None else right)
    left = list([c for c in df.columns if c not in right] if left is None else left)

    sty = df.style
    if formats:
        sty = sty.format(formats)
    sty = sty.hide(axis="index")
    if caption:
        sty = sty.set_caption(caption)

    sty = sty.set_table_styles([
        {"selector": "", "props": [
            ("border-collapse", "collapse"),
            ("font-family", "-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif"),
            ("font-size", "13px"),
            ("margin", "4px 0 12px 0")]},
        {"selector": "caption", "props": [
            ("caption-side", "top"), ("text-align", "left"),
            ("font-weight", "600"), ("font-size", "13.5px"),
            ("color", PALETTE["ink"]), ("padding", "0 0 8px 2px")]},
        {"selector": "th.col_heading", "props": [
            ("background-color", PALETTE["teal"]), ("color", "white"),
            ("font-weight", "600"), ("padding", "7px 14px"),
            ("border", "none"), ("white-space", "nowrap")]},
        {"selector": "td", "props": [
            ("padding", "6px 14px"), ("border", "none"),
            ("color", PALETTE["ink"]), ("white-space", "nowrap")]},
        {"selector": "tbody tr:nth-child(even)", "props": [
            ("background-color", PALETTE["band"])]},
        {"selector": "tbody tr:hover", "props": [
            ("background-color", "#e8eef0")]},
    ])

    # Per-column alignment for both header and body.
    col_styles = {}
    for c in df.columns:
        align = "right" if c in right else "left"
        col_styles[c] = [{"selector": "th", "props": [("text-align", align)]},
                         {"selector": "td", "props": [("text-align", align)]}]
    sty = sty.set_table_styles(col_styles, overwrite=False, axis=0)
    return sty
