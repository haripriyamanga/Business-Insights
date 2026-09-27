"""Matplotlib chart builders for the dashboard.

Each function takes an aggregated DataFrame (from ``analysis.sales_analysis``)
and returns a Matplotlib ``Figure``. Styling is centralized here so every chart
shares the same look: one accent color per chart, hairline gridlines, readable
axis labels and only selective value labels (the full numbers are available in
each chart's data table in the dashboard).
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # render off-screen; Streamlit displays the figure

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, MaxNLocator

from utils.formatting import format_currency, format_number

# Palette (validated categorical slots 1 and 2 on a light surface).
COLORS = {
    "surface": "#fcfcfb",
    "primary": "#2a78d6",  # revenue
    "secondary": "#eb6834",  # orders / units
    "ink": "#0b0b0b",
    "ink_secondary": "#52514e",
    "muted": "#898781",
    "grid": "#e1e0d9",
    "axis": "#c3c2b7",
}

plt.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
        "axes.titlelocation": "left",
        "axes.titlepad": 12,
        "axes.labelcolor": COLORS["ink_secondary"],
        "axes.edgecolor": COLORS["axis"],
        "axes.facecolor": COLORS["surface"],
        "figure.facecolor": COLORS["surface"],
        "text.color": COLORS["ink"],
        "xtick.color": COLORS["muted"],
        "ytick.color": COLORS["muted"],
        "xtick.labelcolor": COLORS["ink_secondary"],
        "ytick.labelcolor": COLORS["ink_secondary"],
    }
)

currency_axis = FuncFormatter(lambda value, _: format_currency(value, compact=True))
count_axis = FuncFormatter(lambda value, _: format_number(round(value)))


def _style_axes(ax, value_axis: str = "y") -> None:
    """Hairline grid on the value axis only; hide the chart box except the baseline."""
    for side in ("top", "right", "left" if value_axis == "y" else "bottom"):
        ax.spines[side].set_visible(False)
    ax.grid(axis=value_axis, color=COLORS["grid"], linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(length=0, pad=6)


def _new_figure(width: float = 6, height: float = 3.6) -> tuple[Figure, plt.Axes]:
    fig, ax = plt.subplots(figsize=(width, height), layout="constrained")
    return fig, ax


def _period_axis_format(ax, periods: pd.Series) -> None:
    """Readable date ticks whatever the time span."""
    locator = mdates.AutoDateLocator(minticks=3, maxticks=9)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    if len(periods) == 1:  # a single point needs some breathing room
        only = periods.iloc[0]
        ax.set_xlim(only - pd.Timedelta(days=20), only + pd.Timedelta(days=20))


def line_chart(
    data: pd.DataFrame,
    x: str,
    y: str,
    title: str,
    y_label: str,
    color: str = COLORS["primary"],
    currency: bool = True,
) -> Figure:
    """A single-series trend line with the peak value labelled."""
    fig, ax = _new_figure()
    show_markers = len(data) <= 36
    ax.plot(
        data[x],
        data[y],
        color=color,
        linewidth=2,
        marker="o" if show_markers else None,
        markersize=5,
        markeredgecolor=COLORS["surface"],
        markeredgewidth=1.5,
    )
    ax.fill_between(data[x], data[y], color=color, alpha=0.08, linewidth=0)
    ax.set_title(title)
    ax.set_ylabel(y_label)
    ax.set_ylim(bottom=0)
    ax.yaxis.set_major_formatter(currency_axis if currency else count_axis)
    _period_axis_format(ax, data[x])
    _style_axes(ax, "y")

    if not data.empty and data[y].max() > 0:
        peak = data.loc[data[y].idxmax()]
        label = format_currency(peak[y], compact=True) if currency else format_number(peak[y])
        ax.annotate(
            f"Peak: {label}",
            xy=(peak[x], peak[y]),
            xytext=(0, 10),
            textcoords="offset points",
            ha="center",
            fontsize=9,
            color=COLORS["ink_secondary"],
        )
        ax.set_ylim(top=data[y].max() * 1.18)
    return fig


def horizontal_bar_chart(
    data: pd.DataFrame,
    label_column: str,
    value_column: str,
    title: str,
    x_label: str,
    color: str = COLORS["primary"],
    currency: bool = True,
    share_column: str | None = None,
) -> Figure:
    """Ranked horizontal bars (largest on top) with value labels at the bar ends.

    Horizontal bars keep long product names readable. Value labels are shown
    because a static image has no hover tooltips.
    """
    rows = max(len(data), 1)
    fig, ax = _new_figure(height=max(2.2, 0.36 * rows + 1.1))
    ordered = data.iloc[::-1]  # matplotlib draws the first bar at the bottom
    bars = ax.barh(ordered[label_column].astype(str), ordered[value_column], color=color, height=0.62)
    ax.set_title(title)
    ax.set_xlabel(x_label)
    ax.xaxis.set_major_formatter(currency_axis if currency else count_axis)
    _style_axes(ax, "x")
    ax.tick_params(axis="y", labelcolor=COLORS["ink"])

    maximum = ordered[value_column].max() if not ordered.empty else 0
    if maximum > 0:
        ax.set_xlim(0, maximum * 1.28)  # room for the labels
    for bar, (_, row) in zip(bars, ordered.iterrows()):
        value = row[value_column]
        text = format_currency(value, compact=True) if currency else format_number(value)
        if share_column:
            text += f"  ({row[share_column]:.0%})"
        ax.annotate(
            text,
            xy=(bar.get_width(), bar.get_y() + bar.get_height() / 2),
            xytext=(4, 0),
            textcoords="offset points",
            va="center",
            fontsize=9,
            color=COLORS["ink_secondary"],
        )
    return fig


def revenue_orders_trend(data: pd.DataFrame, title: str) -> Figure:
    """Revenue and orders as two stacked panels sharing one time axis.

    Two panels (instead of one chart with two y-axes) keep each measure on its
    own honest scale while still lining up the dates.
    """
    fig, (revenue_ax, orders_ax) = plt.subplots(
        2, 1, figsize=(11, 5.2), sharex=True, layout="constrained", height_ratios=[1, 1]
    )
    show_markers = len(data) <= 36
    marker_style = {
        "marker": "o" if show_markers else None,
        "markersize": 4,
        "markeredgecolor": COLORS["surface"],
        "markeredgewidth": 1.2,
    }
    revenue_ax.plot(data["period"], data["revenue"], color=COLORS["primary"], linewidth=2, **marker_style)
    orders_ax.plot(data["period"], data["orders"], color=COLORS["secondary"], linewidth=2, **marker_style)

    revenue_ax.set_title(title)
    revenue_ax.set_ylabel("Revenue")
    orders_ax.set_ylabel("Orders")
    revenue_ax.yaxis.set_major_formatter(currency_axis)
    orders_ax.yaxis.set_major_formatter(count_axis)
    orders_ax.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=5))
    for ax in (revenue_ax, orders_ax):
        ax.set_ylim(bottom=0)
        _style_axes(ax, "y")
    _period_axis_format(orders_ax, data["period"])
    return fig


def close_figure(fig: Figure) -> None:
    """Free a figure's memory once Streamlit has rendered it."""
    plt.close(fig)
