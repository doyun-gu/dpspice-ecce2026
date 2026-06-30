"""Shared, professional plotting style for DPSpice notebooks and figures.

A small, dependency-light house style so every figure and table in the
notebooks (and the README hero image) shares one coherent look: muted,
instrument-grade colours with a teal accent, clean grids, no chartjunk.

Requires the ``viz`` extra (matplotlib)::

    pip install 'dpspice[viz]'

Typical use at the top of a notebook::

    from dpspice.plotting import use_style, PALETTE, table
    use_style()

Then plot as usual; the palette and rcParams are applied globally. Use
``PALETTE`` for explicit series colours and :func:`table` to render a tidy
summary table that matches the figure styling (no pandas needed).
"""
from __future__ import annotations

from typing import Sequence

try:
    import matplotlib as mpl
    import matplotlib.pyplot as plt
except ModuleNotFoundError as exc:  # viz extra not installed
    raise SystemExit(
        f"dpspice.plotting needs the plotting extra (missing: {exc.name}). "
        f"Install it with:  pip install 'dpspice[viz]'"
    )

__all__ = ["PALETTE", "CYCLE", "use_style", "table"]

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

    Idempotent; call once near the top of a notebook. Affects every
    subsequent figure (colours, fonts, grid, spines, legend, dpi).
    """
    mpl.rcParams.update({
        # canvas
        "figure.figsize":   (7.2, 3.4),
        "figure.dpi":       110,
        "figure.facecolor": PALETTE["paper"],
        "savefig.dpi":      150,
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
        "axes.axisbelow":   True,
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


def table(headers: Sequence[str],
          rows: Sequence[Sequence[object]],
          title: str | None = None,
          col_align: Sequence[str] | None = None,
          col_width: Sequence[float] | None = None,
          row_height: float = 0.46):
    """Render a tidy summary table as a matplotlib figure (no pandas).

    Header band in teal with white text, zebra-striped body, sensible
    numeric right-alignment. Returns ``(fig, ax)`` so it embeds as a figure
    in notebook output and matches the surrounding plots.

    Parameters
    ----------
    headers : column titles.
    rows    : sequence of rows (each a sequence of cell values, str()'d).
    title   : optional caption above the table.
    col_align : per-column 'left' | 'center' | 'right'. Defaults: first
        column left, the rest right (the common label-then-numbers layout).
    col_width : relative column widths (auto-sized from content if omitted).
    row_height : per-row height in figure-inches.
    """
    ncols = len(headers)
    body = [[("" if c is None else str(c)) for c in r] for r in rows]

    if col_align is None:
        col_align = ["left"] + ["right"] * (ncols - 1)
    if col_width is None:
        widths = []
        for j in range(ncols):
            cells = [headers[j]] + [r[j] for r in body]
            widths.append(max(len(c) for c in cells))
        total = sum(widths) or 1
        col_width = [w / total for w in widths]

    # Figure width from the widest row's character count, clamped to a sane band.
    content_chars = max([sum(len(h) for h in headers)]
                        + [sum(len(c) for c in r) for r in body])
    fig_w = max(4.2, min(11.0, 0.13 * content_chars + 1.2 * ncols))
    body_h = row_height * (len(body) + 1)        # header + body rows
    title_h = 0.42 if title else 0.0
    fig_h = body_h + title_h

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.axis("off")

    top = body_h / fig_h                          # table fills the lower band
    if title:
        ax.text(0.0, top + (1 - top) * 0.30, title, transform=ax.transAxes,
                ha="left", va="bottom", fontsize=12.5, fontweight="bold",
                color=PALETTE["ink"])

    tbl = ax.table(
        cellText=body,
        colLabels=list(headers),
        colWidths=list(col_width),
        cellLoc="center",
        bbox=[0, 0, 1, top],                      # anchor: no centering gap
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(10)

    align_map = {"left": "left", "center": "center", "right": "right"}
    for (r, c), cell in tbl.get_celld().items():
        cell.set_edgecolor(PALETTE["paper"])
        cell.set_linewidth(2)
        cell.PAD = 0.06
        cell.get_text().set_ha(align_map.get(col_align[c], "center"))
        if r == 0:  # header band
            cell.set_facecolor(PALETTE["teal"])
            cell.get_text().set_color(PALETTE["paper"])
            cell.get_text().set_fontweight("bold")
        else:
            cell.set_facecolor(PALETTE["band"] if r % 2 else PALETTE["paper"])
            cell.get_text().set_color(PALETTE["ink"])
    return fig, ax
